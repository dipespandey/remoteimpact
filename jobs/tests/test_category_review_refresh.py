from django.test import TestCase

from jobs.models import Category
from jobs.services.category_review import category_review_input_hash
from jobs.services.importers.common import _mark_job_seen, _upsert_job


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

    def test_source_cannot_inject_review(self):
        self.job.raw_data = {}
        self.job.save(update_fields=['raw_data'])
        job, _ = _upsert_job({**self.payload, 'raw_data': {'category_source_review': self.review}})
        self.assertNotIn('category_source_review', job.raw_data)
