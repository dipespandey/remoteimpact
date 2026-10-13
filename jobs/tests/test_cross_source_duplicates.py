from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from jobs.models import Job, Organization, JobSlugRedirect
from jobs.services.importers.common import is_duplicate_job, _canonical_duplicate_url


class CrossSourceDuplicatesTests(TestCase):
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
