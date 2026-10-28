from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from jobs.services.importers.common import _upsert_job, _mark_job_seen
from jobs.services.job_quality import job_posting_schema_json
from jobs.services.location_review import location_review_input_hash


class LocationReviewRefreshTests(TestCase):
    def setUp(self):
        self.payload = {
            'source': '80000hours', 'external_id': 'reviewed-role',
            'title': 'Editorial Fellow', 'organization_name': 'Review Employer',
            'application_url': 'https://example.org/jobs/fellow',
            'description': 'Write and report with our editorial team. ' * 10,
            'location': 'Remote, Global',
        }
        self.job, _ = _upsert_job(self.payload)
        self.job.location = 'Washington, D.C. (in-person)'
        self.job.country = 'USA'
        self.review = {
            'version': 1, 'checked_at': timezone.now().isoformat(),
            'source_input_hash': location_review_input_hash(self.payload),
            'url': self.payload['application_url'], 'evidence': 'Washington, D.C. (in-person)',
            'correction': {'location': self.job.location, 'country': self.job.country,
                           'jobLocation': {'address': {'addressCountry': 'US', 'addressLocality': 'Washington'}}},
        }
        self.save_review()

    def save_review(self):
        self.job.raw_data = {'location_source_review': self.review}
        self.job.save(update_fields=['location', 'country', 'raw_data'])

    def test_unchanged_refresh_keeps_reviewed_location_and_physical_schema(self):
        job, _ = _upsert_job(self.payload)
        self.assertEqual(job.location, 'Washington, D.C. (in-person)')
        self.assertEqual(job.country, 'USA')
        schema = job_posting_schema_json(job, 'https://remoteimpact.org')
        self.assertIn('"jobLocation"', schema)
        self.assertNotIn('TELECOMMUTE', schema)
        job, _ = _upsert_job(self.payload)
        self.assertEqual(job.raw_data['location_source_review'], self.review)

    def test_changed_source_input_requires_another_review(self):
        for field, value in [('description', 'New role description. ' * 20),
                             ('location', 'Remote (UK)'), ('title', 'Senior Editorial Fellow'),
                             ('application_url', 'https://example.org/jobs/new-role')]:
            with self.subTest(field=field):
                self.save_review()
                job, _ = _upsert_job({**self.payload, field: value})
                self.assertTrue(job.raw_data['location_source_review']['needs_review'])
                self.assertFalse(job.is_active)
                self.assertNotEqual(job.location, 'Washington, D.C. (in-person)')

    def test_expired_or_future_review_is_not_applied(self):
        for delta in (timedelta(days=-8), timedelta(days=1)):
            with self.subTest(delta=delta):
                self.review['checked_at'] = (timezone.now() + delta).isoformat()
                self.save_review()
                job, _ = _upsert_job(self.payload)
                self.assertTrue(job.raw_data['location_source_review']['needs_review'])
                self.assertFalse(job.is_active)
                _mark_job_seen(job.source, job.external_id)
                job.refresh_from_db()
                self.assertFalse(job.is_active)
                self.assertNotEqual(job.location, 'Washington, D.C. (in-person)')

    def test_incoming_feed_cannot_supply_a_review_override(self):
        self.job.raw_data = {}
        self.job.save(update_fields=['raw_data'])
        job, _ = _upsert_job({**self.payload, 'raw_data': {'location_source_review': self.review}})
        self.assertNotIn('location_source_review', job.raw_data)
        self.assertNotEqual(job.location, 'Washington, D.C. (in-person)')
        new_job, created = _upsert_job({**self.payload, 'external_id': 'new-role',
                                      'organization_name': 'Different Source Employer',
                                      'application_url': 'https://example.org/jobs/new',
                                      'raw_data': {'location_source_review': self.review}})
        self.assertTrue(created)
        self.assertNotIn('location_source_review', new_job.raw_data)
