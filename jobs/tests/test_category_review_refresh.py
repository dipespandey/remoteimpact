from django.test import TestCase
from asgiref.sync import async_to_sync

from jobs.models import Category
from jobs.services.category_review import category_review_input_hash
from jobs.services.importers.common import _mark_job_seen, _upsert_job, batch_upsert_jobs


class CategoryReviewRefreshTests(TestCase):
    def setUp(self):
        self.payload = {
            'source': 'probablygood', 'external_id': 'category-review',
            'title': 'Security Officer', 'organization_name': 'Example Employer',
            'application_url': 'https://example.org/jobs/security',
            'description': 'Support humanitarian security and emergency response. ' * 10,
            'location': 'Remote', 'category_name': 'Communications & Media',
        }
        self.job, _ = _upsert_job(self.payload)
        self.job.category = Category.objects.get_or_create(slug='humanitarian', defaults={'name': 'Humanitarian & Disaster Relief'})[0]
        self.review = {'version': 1, 'source_input_hash': category_review_input_hash(self.payload),
                       'category_slug': 'humanitarian', 'evidence': 'Humanitarian security duties'}
        self.job.raw_data = {'category_source_review': self.review}
        self.job.save(update_fields=['category', 'raw_data'])

    def test_unchanged_refresh_and_incremental_sighting_preserve_review(self):
        _mark_job_seen(self.job.source, self.job.external_id)
        job, _ = _upsert_job(self.payload)
        self.assertEqual(job.category.slug, 'humanitarian')
        self.assertEqual(job.raw_data['category_source_review'], self.review)

    def test_changed_source_requires_review_and_does_not_keep_old_correction(self):
        job, _ = _upsert_job({**self.payload, 'description': 'A different communications role. ' * 20})
        self.assertTrue(job.raw_data['category_source_review']['needs_review'])
        self.assertNotEqual(job.category.slug, 'humanitarian')

    def test_incremental_import_refreshes_a_changed_reviewed_posting(self):
        changed = {**self.payload, 'description': 'A different communications role. ' * 20}
        result = async_to_sync(batch_upsert_jobs)([changed], skip_existing=True, skip_duplicates=False)
        self.job.refresh_from_db()
        self.assertEqual(result['updated'], 1)
        self.assertTrue(self.job.raw_data['category_source_review']['needs_review'])
        self.assertNotEqual(self.job.category.slug, 'humanitarian')

    def test_source_cannot_inject_review(self):
        self.job.raw_data = {}
        self.job.save(update_fields=['raw_data'])
        job, _ = _upsert_job({**self.payload, 'raw_data': {'category_source_review': self.review}})
        self.assertNotIn('category_source_review', job.raw_data)
        job, created = _upsert_job({**self.payload, 'external_id': 'new-record',
                                    'organization_name': 'Another Employer',
                                    'application_url': 'https://example.org/jobs/new',
                                    'raw_data': {'category_source_review': self.review}})
        self.assertTrue(created)
        self.assertNotIn('category_source_review', job.raw_data)

    def test_changed_source_does_not_retain_stale_review_when_category_is_unknown(self):
        job, _ = _upsert_job({**self.payload, 'title': 'Different Security Officer',
                              'category_name': 'Unmapped category'})
        self.assertTrue(job.raw_data['category_source_review']['needs_review'])
        self.assertNotEqual(job.category.slug, 'humanitarian')
