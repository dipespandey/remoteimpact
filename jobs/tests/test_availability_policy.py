import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch, Mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job, Organization
from jobs.services.job_quality import job_posting_schema_json
from jobs.services.source_page_enrichment import PageFetch
from jobs.sitemaps import visible_jobs
from jobs.views.jobs import JobDetailView
from jobs.views.seo_pages import visible_jobs as seo_visible_jobs


@override_settings(SOCIALACCOUNT_PROVIDERS={provider: {"APP": {"client_id": "test", "secret": "test", "key": ""}} for provider in ("google", "linkedin")})
class AvailabilityPolicyTests(TestCase):
    def setUp(self):
        for mocked in [patch("jobs.services.embedding_service.embed_job", return_value=[0.0]*384), patch("jobs.signals.threading.Thread"), patch("allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_provider", return_value=Mock(get_login_url=Mock(return_value="/test-login/")))]:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.org = Organization.objects.create(name='Employer "name" </script>', website='')

    def job(self, **kwargs):
        values = dict(title='Policy Manager', slug=f'policy-{Job.objects.count()}',
                      organization=self.org, source='ashby', description='Manage programs.',
                      application_url='https://jobs.example.org/role')
        values.update(kwargs)
        return Job.objects.create(**values)

    def test_all_public_surfaces_share_freshness_quality_and_deadline(self):
        now = timezone.now()
        examples = [
            (dict(posted_at=now - timedelta(days=100), last_seen_at=now - timedelta(days=1)), True),
            (dict(posted_at=now - timedelta(days=100)), False),
            (dict(posted_at=now - timedelta(days=100), expires_at=now + timedelta(days=1)), True),
            (dict(expires_at=now - timedelta(seconds=1)), False),
            (dict(is_active=False), False),
            (dict(quality_score=54), False),
            (dict(quality_score=55), True),
        ]
        for values, expected in examples:
            job = self.job(location='Remote (US)', **values)
            with self.subTest(values=values):
                self.assertEqual(job.is_available, expected)
                self.assertEqual(visible_jobs().filter(pk=job.pk).exists(), expected)
                self.assertEqual(seo_visible_jobs().filter(pk=job.pk).exists(), expected)
                self.assertEqual(JobDetailView._available_queryset().filter(pk=job.pk).exists(), expected)
                response = self.client.get(reverse('jobs:job_detail', args=[job.slug]))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['job_available'], expected)
                self.assertEqual('"@type": "JobPosting"' in response.content.decode(), expected)

    def test_schema_omits_invented_expiry_identity_and_region_country(self):
        job = self.job(country='Europe', salary_min=Decimal('80000'))
        encoded = job_posting_schema_json(job, 'https://example.org')
        data = json.loads(encoded)
        self.assertNotIn('validThrough', data)
        self.assertNotIn('sameAs', data['hiringOrganization'])
        self.assertNotIn('applicantLocationRequirements', data)
        self.assertNotIn('baseSalary', data)
        self.assertNotIn('</script>', encoded)
        self.assertEqual(data['hiringOrganization']['name'], self.org.name)

    def test_schema_explicit_deadline_country_and_annual_salary(self):
        job = self.job(country='Norway', description='Applicants must be based in Norway. This role is remote in Norway.', expires_at=timezone.now() + timedelta(days=3),
                       salary_min=Decimal('65000'), salary_max=Decimal('80000'), raw_data={'salaryPeriod':'YEAR', 'salaryCurrency':'USD'})
        data = json.loads(job_posting_schema_json(job, 'https://example.org'))
        self.assertEqual(data['validThrough'], job.expires_at.isoformat())
        self.assertEqual(data['applicantLocationRequirements']['name'], 'Norway')
        self.assertEqual(data['baseSalary']['value']['unitText'], 'YEAR')

    def test_explicit_legal_residency_countries_from_idc_listing(self):
        source = ('Candidates must already have the legal right to reside and work in '
                  'Albania, Bosnia and Herzegovina, Greece, Kosovo*, Montenegro, '
                  'North Macedonia or Serbia. IDC cannot sponsor work permits or support relocation.')
        job = self.job(location='Remote', country='', description=source)
        data = json.loads(job_posting_schema_json(job, 'https://example.org'))
        self.assertEqual({c['name'] for c in data['applicantLocationRequirements']},
                         {'Albania', 'Bosnia and Herzegovina', 'Greece', 'Kosovo',
                          'Montenegro', 'North Macedonia', 'Serbia'})
        # Production import retains Greece as the primary country; the legal
        # eligibility statement still requires all seven countries in markup.
        job.country = 'Greece'
        self.assertEqual(json.loads(job_posting_schema_json(job, 'https://example.org'))['applicantLocationRequirements'], data['applicantLocationRequirements'])
        for conflict in [{'country': 'USA'}, {'location': 'Remote (US)'},
                         {'description': source + ' Applicants must be based in Canada.'},
                         {'description': source + ' Except Serbia.'},
                         {'description': 'Candidates must already have the legal right to reside and work in California.'}]:
            with self.subTest(conflict=conflict):
                for key, value in dict(location='Remote', country='', description=source).items():
                    setattr(job, key, value)
                for key, value in conflict.items():
                    setattr(job, key, value)
                self.assertNotIn('applicantLocationRequirements', json.loads(job_posting_schema_json(job, 'https://example.org')))

    def test_legal_residency_variant_and_non_required_mentions(self):
        for source, expected in [
            ('Applicants need to already hold the right to live and work within Norway.', 'Norway'),
            ('We help people obtain the legal right to reside and work in Norway.', None),
            ('Candidates must not have the legal right to reside and work in Norway.', None),
        ]:
            with self.subTest(source=source):
                job = self.job(location='Remote', country='', description=source)
                data = json.loads(job_posting_schema_json(job, 'https://example.org'))
                if expected:
                    self.assertEqual(data['applicantLocationRequirements']['name'], expected)
                else:
                    self.assertNotIn('applicantLocationRequirements', data)

    def test_unknown_or_matching_page_does_not_manufacture_freshness(self):
        for page in [PageFetch('http_403'), PageFetch('ok', 'Policy Manager ' * 20)]:
            job = self.job(last_seen_at=timezone.now() - timedelta(days=100))
            before = job.last_seen_at
            with patch('jobs.management.commands.check_stale_jobs.fetch_job_page', return_value=page):
                call_command('check_stale_jobs', limit=10, delay=0, cooldown_days=0, stdout=StringIO())
            job.refresh_from_db()
            self.assertTrue(job.is_active)
            self.assertEqual(job.last_seen_at, before)
            self.assertEqual(job.raw_data['source_verification']['opening_status'], 'unknown')

    def test_cleanup_agrees_with_listing_for_source_sighting_and_old_post(self):
        now = timezone.now()
        # Imported listings are judged by when a source last showed them; the post date
        # only counts while no sighting has been recorded yet.
        fresh_post = self.job(posted_at=now)
        unseen_for_months = self.job(posted_at=now, last_seen_at=now-timedelta(days=100))
        fresh_sighting = self.job(posted_at=now-timedelta(days=100), last_seen_at=now)
        stale = self.job(posted_at=now-timedelta(days=100))
        call_command('deactivate_expired_jobs', stdout=StringIO())
        for job in [fresh_post, fresh_sighting]:
            job.refresh_from_db()
            self.assertTrue(job.is_active)
        for job in [stale, unseen_for_months]:
            job.refresh_from_db()
            self.assertFalse(job.is_active)

    def test_daily_pipeline_paid_enrichment_is_explicit_opt_in(self):
        for include_ai in (False, True):
            with self.subTest(include_ai=include_ai), patch('jobs.management.commands.run_daily_job_quality.call_command') as mocked:
                call_command('run_daily_job_quality', include_ai=include_ai, stdout=StringIO())
                commands = [call.args[0] for call in mocked.call_args_list]
                self.assertEqual('backfill_skills' in commands, include_ai)
                self.assertEqual('enrich_job_source_pages' in commands, include_ai)
                self.assertIn('check_stale_jobs', commands)

    def test_unsafe_application_link_and_non_usd_currency_render_truthfully(self):
        job = self.job(application_url='javascript:alert(1)', salary_currency='INR', salary_min=Decimal('600000'))
        response = self.client.get(reverse('jobs:job_detail', args=[job.slug]))
        self.assertNotContains(response, 'href="javascript:')
        self.assertContains(response, 'INR 600,000+')
        self.assertEqual(response.context['safe_application_url'], '')

    def test_unknown_link_check_remains_unverified(self):
        for result in ['http_500', 'http_403', 'fetch_error', 'unsafe_url']:
            job = self.job()
            with patch('jobs.management.commands.check_stale_jobs.fetch_job_page', return_value=PageFetch(result)):
                call_command('check_stale_jobs', limit=10, delay=0, cooldown_days=0, stdout=StringIO())
            job.refresh_from_db()
            self.assertTrue(job.is_active)
            self.assertEqual(job.raw_data['source_verification']['status'], 'unverified')
            self.assertEqual(job.raw_data['source_verification']['opening_status'], 'unknown')

    def test_schema_country_metadata_does_not_narrow_us_or_canada_source(self):
        job = self.job(country='United States', description='This role is remote in the United States or Canada. Manage power systems projects.')
        data = json.loads(job_posting_schema_json(job, 'https://example.org'))
        self.assertNotIn('applicantLocationRequirements', data)

    def test_schema_country_metadata_without_residency_evidence_is_omitted(self):
        job = self.job(country='United States', description='Manage education programs for partners in Canada and the US.')
        data = json.loads(job_posting_schema_json(job, 'https://example.org'))
        self.assertNotIn('applicantLocationRequirements', data)

    def test_schema_positive_countries_preserved_when_metadata_not_contradictory(self):
        job = self.job(country='', description='Applicants must be based in the United States or Canada.')
        data = json.loads(job_posting_schema_json(job, 'https://example.org'))
        self.assertEqual({item['name'] for item in data['applicantLocationRequirements']}, {'United States of America', 'Canada'})

    def test_schema_region_or_state_constraint_does_not_expand_to_whole_country(self):
        for description in ['Applicants must be based in Europe.', 'This role is remote in California, United States.']:
            job = self.job(country='United States', description=description)
            data = json.loads(job_posting_schema_json(job, 'https://example.org'))
            self.assertNotIn('applicantLocationRequirements', data)

    def test_duplicate_source_sections_render_once_without_changing_source(self):
        text = 'Plan and manage impact programs with careful reporting and meaningful education initiatives. ' * 3
        job = self.job(description='<p>'+text+'</p>', requirements=text, impact=text)
        response = self.client.get(reverse('jobs:job_detail', args=[job.slug]))
        self.assertContains(response, 'Job description')
        self.assertNotContains(response, 'What makes you a great fit')
        self.assertNotContains(response, "The difference you'll make")
        job.refresh_from_db()
        self.assertEqual(job.requirements, text)
        self.assertEqual(job.impact, text)


class SchemaLabelCountryTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Label Org')

    def job(self, **kwargs):
        values = dict(title='Grid Engineer', slug=f'grid-{Job.objects.count()}', organization=self.org, source='ashby',
                      description='<p>Design grid upgrades.</p><ul><li>Model loads</li><li>Write reports</li></ul>',
                      application_url='https://jobs.example.org/role', location='Remote (US)')
        values.update(kwargs)
        return Job.objects.create(**values)

    def test_visible_label_gives_country_when_text_is_silent(self):
        data = json.loads(job_posting_schema_json(self.job(), 'https://example.org'))
        self.assertEqual(data['applicantLocationRequirements']['name'], 'United States of America')
        data = json.loads(job_posting_schema_json(self.job(location='Remote (EU)'), 'https://example.org'))
        self.assertEqual(len(data['applicantLocationRequirements']), 27)

    def test_residency_wording_leaves_it_to_the_strict_rules(self):
        job = self.job(description='This role is remote in the United States or Canada.')
        self.assertNotIn('applicantLocationRequirements', json.loads(job_posting_schema_json(job, 'https://example.org')))

    def test_worldwide_remote_and_descriptions_keep_breaks(self):
        data = json.loads(job_posting_schema_json(self.job(location='Remote'), 'https://example.org'))
        self.assertNotIn('applicantLocationRequirements', data)
        self.assertIn('Design grid upgrades.\n', data['description'])
        self.assertIn('- Model loads', data['description'])
        self.assertEqual(data['identifier']['name'], 'Label Org')


class GoogleJobPostingTests(TestCase):
    def test_remote_job_without_country_omits_unknown_eligibility(self):
        from jobs.services.job_quality import google_job_posting_json
        org = Organization.objects.create(name='Worldwide Org')
        worldwide = Job.objects.create(title='Writer', slug='writer-ww', organization=org, location='Remote',
                                       description='Write.', application_url='https://example.org/a')
        us = Job.objects.create(title='Writer', slug='writer-us', organization=org, location='Remote (US)',
                                description='Write.', application_url='https://example.org/b')
        data = json.loads(google_job_posting_json(worldwide, 'https://example.org'))
        self.assertEqual(data['@type'], 'JobPosting')
        self.assertNotIn('applicantLocationRequirements', data)
        self.assertIn('"JobPosting"', google_job_posting_json(us, 'https://example.org'))
