from asgiref.sync import async_to_sync
from django.test import TestCase
from unittest.mock import patch

from jobs.models import Job, Organization
from jobs.services.importers.common import batch_upsert_jobs, _mark_job_seen
from jobs.services.importers.idealist import _build_idealist_payload, _transform_idealist_hit
from jobs.views.seo_pages import nonprofit_inventory, visible_jobs


class IdealistOrganizationClassificationTests(TestCase):
    def setUp(self):
        for mocked in [patch('jobs.services.embedding_service.embed_job', return_value=[0.0] * 384)]:
            mocked.start(); self.addCleanup(mocked.stop)
        self.org = Organization.objects.create(name='Community Support', slug='community-support')
        self.job = Job.objects.create(title='Program Manager', slug='source-type-refresh',
            organization=self.org, source='idealist', external_id='a' * 32,
            description='A current source description. ' * 8,
            raw_data={'objectID': 'a' * 32, 'orgName': self.org.name,
                      'quality_enrichment': {'salary': {'method': 'keep-me'}}})

    def payload(self, org_type=None):
        hit = {'objectID': self.job.external_id, 'name': self.job.title,
               'orgName': self.org.name, 'orgID': 'b' * 32,
               'description': 'Incoming description should be skipped. ' * 8}
        if org_type is not None:
            hit['orgType'] = org_type
        return _transform_idealist_hit(hit)

    def test_incremental_import_refreshes_classification_without_rewriting_job_or_profile(self):
        requested = _build_idealist_payload()['requests'][0]['attributesToRetrieve']
        self.assertIn('orgType', requested)
        self.assertIn('orgID', requested)
        original_description = self.job.description
        original_posted = self.job.posted_at
        for value, expected_inclusion in [('NONPROFIT', True), ('CORPORATION', False), (None, False)]:
            result = async_to_sync(batch_upsert_jobs)([self.payload(value)], use_ai=False,
                skip_existing=True, skip_duplicates=False)
            self.assertEqual(result['skipped'], 1)
            self.job.refresh_from_db(); self.org.refresh_from_db()
            self.assertEqual(self.job.description, original_description)
            self.assertEqual(self.job.posted_at, original_posted)
            self.assertEqual(self.org.organization_type, '')
            self.assertEqual(self.job.raw_data['quality_enrichment']['salary']['method'], 'keep-me')
            self.assertEqual(nonprofit_inventory(visible_jobs()).filter(pk=self.job.pk).exists(), expected_inclusion)
        self.assertNotIn('orgType', self.job.raw_data)

    def test_mismatched_source_identity_cannot_change_classification(self):
        for incoming in [
            {'objectID': 'other', 'orgName': self.org.name, 'orgType': 'NONPROFIT'},
            {'objectID': self.job.external_id, 'orgName': 'Other Employer', 'orgType': 'NONPROFIT'},
        ]:
            _mark_job_seen('idealist', self.job.external_id, incoming)
            self.job.refresh_from_db()
            self.assertNotIn('orgType', self.job.raw_data)
