from asgiref.sync import async_to_sync
from django.test import TestCase
from django.db.models.fields.json import KeyTransform
from types import SimpleNamespace
from jobs.models import Job

from jobs.services.category_review import category_review_input_hash
from jobs.services.importers.common import _upsert_job, batch_upsert_jobs
from jobs.services.job_quality import normalize_salary, supported_base_salary
from jobs.services.scout_evidence import compensation, compensation_currency, compensation_display


class CompensationReviewRefreshTests(TestCase):
    def setUp(self):
        self.payload = {
            'source': 'idealist', 'external_id': 'pay-review',
            'title': 'Clinical Director', 'organization_name': 'Example Employer',
            'application_url': 'https://example.org/jobs/director',
            'description': 'Part-time role. Salary is £60,000 full-time equivalent. ' * 8,
            'location': 'Remote (UK)', 'salary_min': 60000, 'salary_max': 60000,
            'salary_currency': 'GBP',
            'raw_data': {'salaryPeriod': 'YEAR', 'salaryMinimum': 60000,
                         'salaryMaximum': 60000, 'salaryCurrency': 'GBP'},
        }
        self.job, _ = _upsert_job(self.payload)
        raw = dict(self.job.raw_data)
        raw['compensation_source_review'] = {'version': 1, 'action': 'suppress',
            'source_input_hash': category_review_input_hash(self.payload), 'needs_review': False}
        self.job.raw_data = raw
        self.job.salary_min = self.job.salary_max = None
        self.job.salary_currency = ''
        self.job.save(update_fields=['raw_data', 'salary_min', 'salary_max', 'salary_currency'])

    def test_full_refresh_and_quality_cleanup_keep_pay_hidden(self):
        job, _ = _upsert_job(self.payload)
        self.assertIsNone(job.salary_min)
        self.assertIsNone(job.salary_max)
        self.assertEqual(compensation(job)[:2], (None, None))
        self.assertEqual(compensation_display(job), '')
        self.assertIsNone(supported_base_salary(job))
        job.salary_min = 60000
        normalize_salary(job)
        self.assertIsNone(job.salary_min)

    def test_scout_projection_hides_original_raw_amount(self):
        projected = SimpleNamespace(scout_compensation_review={'version': 1, 'action': 'suppress'},
            scout_source_period='YEAR', scout_source_pay_min=60000, scout_source_pay_max=60000,
            scout_source_currency='USD', salary_min=None, salary_max=None, salary_currency='', description='')
        self.assertEqual(compensation_display(projected), '')
        self.assertEqual(compensation(projected)[:2], (None, None))
        self.assertEqual(compensation_currency(projected), '')
        loaded = Job.objects.annotate(
            scout_compensation_review=KeyTransform('compensation_source_review', 'raw_data'),
            scout_source_pay_min=KeyTransform('salaryMinimum', 'raw_data'),
            scout_source_pay_max=KeyTransform('salaryMaximum', 'raw_data'),
            scout_source_period=KeyTransform('salaryPeriod', 'raw_data'),
        ).defer('raw_data').get(pk=self.job.pk)
        self.assertEqual(compensation_display(loaded), '')
        self.assertEqual(compensation(loaded)[:2], (None, None))

    def test_incremental_refresh_marks_changed_source_for_review(self):
        changed = {**self.payload, 'description': 'Changed role and pay details. ' * 20}
        result = async_to_sync(batch_upsert_jobs)([changed], skip_existing=True, skip_duplicates=False)
        self.job.refresh_from_db()
        self.assertEqual(result['updated'], 1)
        self.assertTrue(self.job.raw_data['compensation_source_review']['needs_review'])
        self.assertIsNone(self.job.salary_min)
        self.assertEqual(compensation_display(self.job), '')

    def test_feed_cannot_create_a_trusted_suppression(self):
        self.job.raw_data = {}
        self.job.save(update_fields=['raw_data'])
        injected = {**self.payload, 'raw_data': {'compensation_source_review': {'version': 1, 'action': 'suppress'}}}
        job, _ = _upsert_job(injected)
        self.assertNotIn('compensation_source_review', job.raw_data)
        new, created = _upsert_job({**injected, 'external_id': 'new',
            'organization_name': 'Different Employer', 'application_url': 'https://example.org/new'})
        self.assertTrue(created)
        self.assertNotIn('compensation_source_review', new.raw_data)
