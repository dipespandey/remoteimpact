from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from jobs.models import FeatureTrial, Organization, OutreachSend
from jobs.tests.test_feature_trials import clicks, make_job


@override_settings(SECURE_SSL_REDIRECT=False, SITE_URL="https://remoteimpact.org")
class TrialResultsEmailTests(TestCase):
    def setUp(self):
        self.send = patch("jobs.management.commands.send_trial_results.email_service.send_email", return_value=True).start()
        self.addCleanup(patch.stopall)
        self.org = Organization.objects.create(name="Animal Advocacy Careers", slug="aac")
        self.job = self.trial(self.org, "Operations Associate", "ops", before=17, during=34)

    def trial(self, org, title, slug, before, during):
        job = make_job(org, title, slug)
        FeatureTrial.objects.create(job=job, started_at=timezone.now() - timedelta(days=6), ends_at=timezone.now() + timedelta(days=8))
        clicks(job, before, days_ago=9, views=43)
        clicks(job, during, days_ago=3, views=96)
        return job

    def run_command(self, *args):
        out = StringIO()
        call_command("send_trial_results", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_shows_the_numbers_and_sends_nothing(self):
        out = self.run_command("--to", "aac=Info@AAC.org")
        self.assertIn("Subject: Animal Advocacy Careers: 34 applicant clicks in 6 days while featured", out)
        self.assertIn("43 views, 17 people clicked through", out)
        self.assertIn("96 views, 34 people clicked through", out)
        self.assertIn("https://remoteimpact.org/jobs/ops/feature/?utm_source=email", out)
        self.send.assert_not_called()
        self.assertFalse(OutreachSend.objects.exists())

    def test_each_address_gets_it_once(self):
        self.run_command("--to", "aac=info@aac.org", "--send")
        self.run_command("--to", "aac=info@aac.org", "--send")
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.send.call_args.kwargs["to"], "info@aac.org")
        self.assertIn("$49 for 30 more days", self.send.call_args.kwargs["text"])
        self.assertEqual(OutreachSend.objects.get().campaign, "trial-results-2026-11")

    def test_roles_without_a_lift_are_skipped(self):
        flat = Organization.objects.create(name="Flat Org", slug="flat")
        self.trial(flat, "Analyst", "analyst", before=5, during=5)
        out = self.run_command("--to", "flat=jobs@flat.org", "--to", "nobody=a@b.org", "--send")
        self.assertIn("Skip flat: no lift", out)
        self.assertIn("Skip nobody: no running trial", out)
        self.send.assert_not_called()

    def test_a_refused_send_can_be_retried(self):
        self.send.return_value = False
        self.run_command("--to", "aac=info@aac.org", "--send")
        self.assertFalse(OutreachSend.objects.exists())
