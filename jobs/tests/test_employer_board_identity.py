from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from jobs.management.commands.import_employer_boards import Command
from jobs.models import Job
from jobs.services.ats_boards import AtsRef, Listing
from jobs.services.importers.common import _upsert_job


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
