from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from jobs.management.commands.import_employer_boards import Command
from jobs.models import Job
from jobs.services.ats_boards import AtsRef, Listing
from jobs.services.importers.common import _upsert_job
from jobs.services.job_quality import job_posting_schema_json
import json


class EmployerBoardIdentityTests(TestCase):
    def setUp(self):
        mock = patch('jobs.services.embedding_service.embed_job',return_value=[0.0]*384)
        mock.start();self.addCleanup(mock.stop)

    def payload(self, board, employer, title):
        entry={'ref':AtsRef('bamboohr',board,'60'),'org_name':employer,'category_name':None}
        listing=Listing('60',title,f'https://{board}.bamboohr.com/careers/60',
            'Manage a research program and coordinate operations for an impact organization. '*8,
            'Remote (US)','remote')
        return Command._payload(entry,listing,listing.description_html,timezone.now().isoformat())

    def test_same_number_on_different_employer_boards_creates_independent_roles(self):
        first,created = _upsert_job(self.payload('amini','Amini','PMO Lead'))
        second,created = _upsert_job(self.payload('occrp','OCCRP','Editor, Moldova'))
        first.refresh_from_db()
        self.assertNotEqual(first.pk,second.pk)
        self.assertEqual(first.title,'PMO Lead')
        self.assertEqual(first.organization.name,'Amini')
        self.assertEqual(first.external_id,'amini:60')
        self.assertEqual(second.external_id,'occrp:60')
        self.assertEqual(Job.objects.filter(source='bamboohr').count(),2)

    def test_first_seen_fallback_does_not_refresh_an_existing_publication_date(self):
        payload=self.payload('amini','Amini','PMO Lead')
        job,created=_upsert_job(payload)
        original=timezone.now()-timedelta(days=20)
        Job.objects.filter(pk=job.pk).update(posted_at=original)
        refreshed,created=_upsert_job(self.payload('amini','Amini','PMO Lead'))
        self.assertFalse(created)
        self.assertEqual(refreshed.posted_at,original)
        self.assertGreater(refreshed.last_seen_at,original)

    def test_matching_legacy_board_row_adopts_scoped_identity(self):
        payload=self.payload('occrp','OCCRP','Editor, Moldova')
        legacy_payload={**payload,'external_id':'60'}
        legacy,created=_upsert_job(legacy_payload)
        updated,created=_upsert_job(payload)
        self.assertFalse(created)
        self.assertEqual(updated.pk,legacy.pk)
        self.assertEqual(updated.external_id,'occrp:60')

    def test_explicit_remote_workplace_survives_country_only_location(self):
        entry = {'ref': AtsRef('ashby', 'example', 'role-1'),
                 'org_name': 'Remote Employer', 'category_name': None}
        listing = Listing('role-1', 'Engineer', 'https://jobs.ashbyhq.com/example/role-1',
                          'Build reliable software for climate teams. ' * 12,
                          'United States', 'remote')
        payload = Command._payload(entry, listing, listing.description_html, timezone.now().isoformat())
        self.assertEqual(payload['location'], 'Remote (United States)')
        job, _ = _upsert_job(payload)
        self.assertIn('Remote', job.location)
        schema = json.loads(job_posting_schema_json(job, 'https://remoteimpact.org'))
        self.assertEqual(schema['jobLocationType'], 'TELECOMMUTE')
        self.assertEqual(schema['applicantLocationRequirements']['name'], 'United States of America')

    def test_workplace_label_does_not_invent_remote_for_other_modes(self):
        entry = {'ref': AtsRef('ashby', 'example', 'role-2'),
                 'org_name': 'Example Employer', 'category_name': None}
        for workplace in ('onsite', 'hybrid', None):
            with self.subTest(workplace=workplace):
                listing = Listing('role-2', 'Engineer', 'https://example.org/job/2',
                                  'Role description.', 'Seattle', workplace)
                payload = Command._payload(entry, listing, listing.description_html, timezone.now().isoformat())
                self.assertEqual(payload['location'], 'Seattle')
