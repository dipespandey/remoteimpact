import io
from unittest.mock import patch

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from jobs.models import Job, JobDailyStat, Organization, OutreachSend
from jobs.tests.test_employer_product import DESCRIPTION, EmployerTestBase


@override_settings(SITE_URL="https://remoteimpact.org")
class EmployerOfferTests(EmployerTestBase):
    def setUp(self):
        super().setUp()
        self.org.homepage_url = "https://climate.example.org"
        self.org.slug = "climate-org"
        self.org.save()
        JobDailyStat.objects.create(job=self.job, date=timezone.localdate(), views=40, apply_clicks=6)
        quiet = Organization.objects.create(name="Quiet Org", slug="quiet-org", homepage_url="https://quiet.example.org")
        quiet_job = Job.objects.create(title="Analyst", slug="analyst-quiet-org", organization=quiet,
                                       description=DESCRIPTION, application_url="https://quiet.example.org/a", quality_score=80)
        JobDailyStat.objects.create(job=quiet_job, date=timezone.localdate(), views=9, apply_clicks=0)
        self.send = patch("jobs.management.commands.send_employer_offer.email_service.send_email", return_value=True).start()
        patch("jobs.management.commands.send_employer_offer.site_email",
              side_effect=lambda org: f"jobs@{org.homepage_url.split('//')[1]}").start()
        self.addCleanup(patch.stopall)

    def run_command(self, *args):
        out = io.StringIO()
        call_command("send_employer_offer", *args, stdout=out)
        return out.getvalue()

    def test_only_employers_with_apply_clicks_are_pitched_with_their_numbers(self):
        self.run_command("--send")
        self.assertEqual(self.send.call_count, 1)
        sent = self.send.call_args.kwargs
        self.assertEqual(sent["to"], "jobs@climate.example.org")
        self.assertIn("viewed 40 times, and 6 people clicked through to apply", sent["text"])
        self.assertIn("/jobs/program-manager-climate-org/feature/?utm_source=email&utm_campaign=employer-offer-2026-11", sent["text"])
        self.assertNotIn("&amp;", sent["text"])

    def test_websites_are_looked_up_once_per_month(self):
        from django.core.cache import cache
        from jobs.management.commands import send_employer_offer
        cache.clear()
        self.run_command()
        self.run_command()
        self.assertEqual(send_employer_offer.site_email.call_count, 1)

    def test_dry_run_sends_nothing(self):
        self.assertIn("Would email Climate Org", self.run_command())
        self.send.assert_not_called()

    def test_an_employer_is_never_pitched_twice(self):
        self.run_command("--send")
        self.run_command("--send")
        self.assertEqual(self.send.call_count, 1)

    def test_employers_reached_by_an_earlier_campaign_are_skipped(self):
        OutreachSend.objects.create(campaign="relaunch-2026-10-employers", email="info@climate.example.org", organization=self.org)
        self.run_command("--send")
        self.send.assert_not_called()

    def test_claimed_employers_are_left_to_the_monthly_report(self):
        self.org.members.add(self.make_user())
        self.send.reset_mock()
        self.run_command("--send")
        self.send.assert_not_called()

    def make_user(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user("member", "member@climate.example.org", "pw")
