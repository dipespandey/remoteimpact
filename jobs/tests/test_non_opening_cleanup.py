from django.core.management import call_command
from django.test import TestCase

from jobs.models import Job, Organization


class NonOpeningCleanupTests(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name="Cleanup Test Organization")
        self.pool = Job.objects.create(
            title="Join Our Talent Community",
            organization=organization,
            source=Job.Source.CLIMATEBASE,
            external_id="cleanup-pool",
            slug="cleanup-pool",
        )
        self.role = Job.objects.create(
            title="Software Engineer",
            organization=organization,
            source=Job.Source.CLIMATEBASE,
            external_id="cleanup-role",
            slug="cleanup-role",
        )
        self.manual_pool = Job.objects.create(
            title="General Application",
            organization=organization,
            source=Job.Source.MANUAL,
            external_id="cleanup-manual-pool",
            slug="cleanup-manual-pool",
        )

    def test_cleanup_deactivates_only_imported_non_openings(self):
        call_command("deactivate_non_opening_jobs")

        self.pool.refresh_from_db()
        self.role.refresh_from_db()
        self.manual_pool.refresh_from_db()
        self.assertFalse(self.pool.is_active)
        self.assertTrue(self.role.is_active)
        self.assertTrue(self.manual_pool.is_active)
