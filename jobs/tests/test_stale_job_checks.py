from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from jobs.models import Job, Organization
from jobs.services.source_page_enrichment import PageFetch


class StaleJobChecksTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Source Test")
        self.job = Job.objects.create(
            title="Python Engineer", slug="stale-check", organization=org,
            source="ashby", application_url="https://jobs.example.org/role",
            description="Python services. " * 20,
        )

    def run_check(self, result, **kwargs):
        with patch("jobs.management.commands.check_stale_jobs.fetch_job_page", return_value=result) as fetch:
            call_command("check_stale_jobs", limit=1, delay=0, stdout=StringIO(), **kwargs)
        self.job.refresh_from_db()
        return fetch

    def test_blocked_response_is_unverified_not_dead_or_live(self):
        self.run_check(PageFetch("http_403"))
        self.assertTrue(self.job.is_active)
        self.assertEqual(self.job.raw_data["source_verification"]["status"], "unverified")

    def test_dead_link_is_deactivated_and_kept_historically(self):
        self.run_check(PageFetch("http_410"))
        self.assertFalse(self.job.is_active)
        self.assertTrue(self.job.raw_data["source_verification"]["was_active"])

    def test_dry_run_does_not_touch_dead_record(self):
        self.run_check(PageFetch("http_404"), dry_run=True)
        self.assertTrue(self.job.is_active)
        self.assertNotIn("last_stale_check", self.job.raw_data or {})

    def test_recently_checked_record_is_not_repeated(self):
        self.job.raw_data = {"last_stale_check": timezone.now().isoformat()}
        self.job.save(update_fields=["raw_data"])
        fetch = self.run_check(PageFetch("ok", "Python Engineer " * 20))
        fetch.assert_not_called()

    def test_matching_page_records_check_without_refreshing_feed_sighting(self):
        self.job.last_seen_at = timezone.now() - timedelta(days=100)
        self.job.save(update_fields=["last_seen_at"])
        before = self.job.last_seen_at
        self.run_check(PageFetch("ok", "Python Engineer " * 20))
        self.assertEqual(self.job.last_seen_at, before)
        self.assertEqual(self.job.raw_data["source_verification"]["status"], "title_confirmed")
        self.assertEqual(self.job.raw_data["source_verification"]["opening_status"], "unknown")
        self.assertIn("checked_at", self.job.raw_data["source_verification"])

    def test_generic_board_redirect_is_not_verified(self):
        self.run_check(PageFetch("ok", "Welcome to all our current vacancies. " * 20))
        self.assertEqual(self.job.raw_data["source_verification"]["status"], "unverified")

    def test_unchecked_jobs_take_priority_over_previously_checked_old_jobs(self):
        self.job.raw_data = {"last_stale_check": (timezone.now() - timedelta(days=10)).isoformat()}
        self.job.save(update_fields=["raw_data"])
        new = Job.objects.create(title="Policy Analyst", slug="unchecked-policy", organization=self.job.organization,
                                 source="ashby", application_url="https://jobs.example.org/policy")
        with patch("jobs.management.commands.check_stale_jobs.fetch_job_page", return_value=PageFetch("http_403")) as fetch:
            call_command("check_stale_jobs", limit=1, delay=0, stdout=StringIO())
        self.assertEqual(fetch.call_args.args[0], new.application_url)
