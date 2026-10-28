from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from jobs.models import Job, Organization, JobSlugRedirect
from jobs.services.importers.common import is_duplicate_job, _canonical_duplicate_url, _mark_job_seen, _upsert_job


class CrossSourceDuplicatesTests(TestCase):
    def retired_pair(self):
        org = Organization.objects.create(name='Impact Employer', slug='impact-employer')
        keeper = Job.objects.create(title='Operations Director', slug='keeper', organization=org,
            source='probablygood', external_id='keeper', application_url='https://example.org/director', quality_score=90)
        duplicate = Job.objects.create(title=keeper.title, slug='retired', organization=org,
            source='80000hours', external_id='retired', application_url=keeper.application_url+'?utm_source=board',
            quality_score=80, is_active=False)
        JobSlugRedirect.objects.create(old_slug=duplicate.slug, job=keeper)
        return keeper, duplicate

    def refresh_payload(self, job, **overrides):
        return dict(source=job.source, external_id=job.external_id, title=job.title,
            organization_name=job.organization.name, location='Remote',
            description='Manage operations and improve financial processes for an impact organization. '*4,
            application_url=job.application_url, **overrides)

    def test_feed_sighting_keeps_proved_duplicate_retired(self):
        keeper, duplicate = self.retired_pair()
        _mark_job_seen(duplicate.source, duplicate.external_id)
        duplicate.refresh_from_db()
        self.assertFalse(duplicate.is_active)
        self.assertIsNotNone(duplicate.last_seen_at)

    def test_full_refresh_keeps_campaign_duplicate_retired(self):
        keeper, duplicate = self.retired_pair()
        payload = self.refresh_payload(duplicate)
        payload['application_url'] = keeper.application_url+'?utm_source=new-board'
        updated, created = _upsert_job(payload)
        self.assertFalse(created)
        self.assertFalse(updated.is_active)
        self.assertIn('financial processes', updated.description)

    def test_changed_posting_destination_can_reopen(self):
        keeper, duplicate = self.retired_pair()
        payload = self.refresh_payload(duplicate)
        payload['application_url'] = 'https://example.org/new-director'
        updated, created = _upsert_job(payload)
        self.assertTrue(updated.is_active)

    def test_closed_keeper_does_not_hide_a_reopened_source(self):
        keeper, duplicate = self.retired_pair()
        keeper.is_active = False
        keeper.save()
        _mark_job_seen(duplicate.source, duplicate.external_id)
        duplicate.refresh_from_db()
        self.assertTrue(duplicate.is_active)

    def test_same_ats_posting_with_org_spelling_variants_keeps_richer_record(self):
        first_org = Organization.objects.create(name="FAR AI", slug="far-ai-rich")
        second_org = Organization.objects.create(name="Far.Ai", slug="far-ai-thin")
        url = "https://jobs.ashbyhq.com/far.ai/a94080f7-288b-4a98-ad4a-aaaa4bbf9968"
        rich = Job.objects.create(title="Software Engineer", slug="rich-ats-copy", organization=first_org, source="80000hours", application_url=url + "?utm_source=80000hours", quality_score=90)
        duplicate = Job.objects.create(title="Software Engineer", slug="thin-ats-copy", organization=second_org, source="ashby", application_url=url, quality_score=60)
        separate = Job.objects.create(title="Software Engineer", slug="separate-ats-role", organization=second_org, source="ashby", application_url=url.replace("a94080f7", "a5cc0d32"), quality_score=90)
        self.assertTrue(is_duplicate_job(url + "?embed=js", source="probablygood", title="Software Engineer", organization_name="FAR.AI"))
        call_command("deactivate_duplicate_jobs", strategy="canonical-url", dry_run=True, stdout=StringIO())
        self.assertFalse(JobSlugRedirect.objects.exists())
        duplicate.refresh_from_db()
        self.assertTrue(duplicate.is_active)
        call_command("deactivate_duplicate_jobs", strategy="canonical-url", stdout=StringIO())
        duplicate.refresh_from_db()
        rich.refresh_from_db()
        separate.refresh_from_db()
        self.assertFalse(duplicate.is_active)
        self.assertTrue(rich.is_active)
        self.assertTrue(separate.is_active)
        self.assertEqual(JobSlugRedirect.objects.get(old_slug=duplicate.slug).job_id, rich.pk)

    def test_employer_campaign_copies_merge_but_functional_ids_do_not(self):
        org = Organization.objects.create(name='Impact Employer', slug='impact-employer')
        first = Job.objects.create(title='Operations Director', slug='original-director', organization=org,
            source='probablygood', application_url='https://example.org/director', quality_score=90)
        second = Job.objects.create(title=first.title, slug='campaign-director', organization=org,
            source='80000hours', application_url=first.application_url+'?utm_source=board', quality_score=80)
        self.assertTrue(is_duplicate_job(second.application_url, source=second.source,
            title=first.title, organization_name=org.name))
        call_command('deactivate_duplicate_jobs', strategy='canonical-url', stdout=StringIO())
        second.refresh_from_db()
        self.assertFalse(second.is_active)
        self.assertEqual(JobSlugRedirect.objects.get(old_slug=second.slug).job_id, first.pk)
        self.assertNotEqual(_canonical_duplicate_url('https://example.org/role?id=1'),
                            _canonical_duplicate_url('https://example.org/role?id=2'))
        self.assertNotEqual(_canonical_duplicate_url('https://example.org/Role'),
                            _canonical_duplicate_url('https://example.org/role'))
        self.assertEqual(_canonical_duplicate_url('https://example.org/careers'), '')
        self.assertEqual(_canonical_duplicate_url('https://org.bamboohr.com/careers/42?source=board'),
                         _canonical_duplicate_url('https://org.bamboohr.com/careers/42'))

    def test_strict_posting_id_survives_title_corrections_and_feed_refresh(self):
        from jobs.services.importers.common import _mark_job_seen
        org = Organization.objects.create(name='Impact Employer', slug='posting-id-employer')
        url = 'https://jobs.ashbyhq.com/employer/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        keeper = Job.objects.create(title='Science Communications Officer, Innovation', slug='updated-title',
            organization=org, source='ashby', application_url=url, quality_score=90)
        old = Job.objects.create(title='Science Communications Officer', slug='old-title',
            organization=org, source='probablygood', external_id='old-title', application_url=url+'?utm_source=board', quality_score=60)
        self.assertTrue(is_duplicate_job(url, source='probablygood', title=old.title, organization_name=org.name))
        call_command('deactivate_duplicate_jobs', strategy='canonical-url', stdout=StringIO())
        old.refresh_from_db()
        self.assertFalse(old.is_active)
        self.assertEqual(JobSlugRedirect.objects.get(old_slug=old.slug).job_id, keeper.pk)
        _mark_job_seen(old.source, old.external_id)
        old.refresh_from_db()
        self.assertFalse(old.is_active)

    def test_application_routes_and_language_variants_preserve_actual_posting_ids(self):
        pairs=[('https://apply.workable.com/j/1EF50F64B4', 'https://apply.workable.com/j/1EF50F64B4/apply?source=Climatebase'),
               ('https://employer.jobs.personio.de/job/2832457', 'https://employer.jobs.personio.de/job/2832457?display=en&gh_src=Climatebase&language=en'),
               ('https://employer.recruitee.com/o/editor', 'https://employer.recruitee.com/o/editor/c/new?source=Climatebase')]
        for first,second in pairs:
            self.assertEqual(_canonical_duplicate_url(first), _canonical_duplicate_url(second))
        self.assertNotEqual(_canonical_duplicate_url(pairs[0][0]), _canonical_duplicate_url(pairs[0][0].replace('1EF50F64B4','D1701EEC39')))
        self.assertNotEqual(_canonical_duplicate_url(pairs[1][0]), _canonical_duplicate_url(pairs[1][0].replace('2832457','2828413')))
        self.assertEqual(_canonical_duplicate_url('https://apply.workable.com/employer/'), '')

    def test_description_match_cannot_merge_distinct_ats_requisitions(self):
        org = Organization.objects.create(name='AECOM', slug='aecom-requisitions')
        description = 'Design utility-scale physical substations and prepare engineering calculations. ' * 5
        jobs = [Job.objects.create(title='Substation Engineer', slug=f'substation-{number}',
            organization=org, source='climatebase', location='Remote (US)', country='USA',
            application_url=f'https://jobs.smartrecruiters.com/AECOM2/{number}-substation-engineer',
            description=description, raw_data={'description': description})
            for number in ('744000144376089', '744000153817469')]
        for strategy in ('all', 'raw-description'):
            with self.subTest(strategy=strategy):
                call_command('deactivate_duplicate_jobs', strategy=strategy, stdout=StringIO())
                self.assertEqual(Job.objects.filter(pk__in=[job.pk for job in jobs], is_active=True).count(), 2)
                self.assertFalse(JobSlugRedirect.objects.filter(old_slug__in=[job.slug for job in jobs]).exists())

    def test_default_strategy_still_merges_same_ats_posting_campaign_copies(self):
        org = Organization.objects.create(name='AECOM', slug='aecom-campaign-copy')
        url = 'https://jobs.smartrecruiters.com/AECOM2/744000144376089-substation-engineer'
        first = Job.objects.create(title='Substation Engineer', slug='substation-original',
            organization=org, source='climatebase', application_url=url, quality_score=90)
        copy = Job.objects.create(title=first.title, slug='substation-campaign',
            organization=org, source='climatebase', application_url=url+'?utm_source=another-board', quality_score=50)
        call_command('deactivate_duplicate_jobs', stdout=StringIO())
        first.refresh_from_db()
        copy.refresh_from_db()
        self.assertTrue(first.is_active)
        self.assertFalse(copy.is_active)
        self.assertEqual(JobSlugRedirect.objects.get(old_slug=copy.slug).job_id, first.pk)
