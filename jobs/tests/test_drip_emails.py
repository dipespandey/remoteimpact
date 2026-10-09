from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from jobs.models import DripEmailLog, JobAlert


User = get_user_model()


class ScoutDripEmailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="scout-drip@example.com",
            username="scout-drip",
            password="test-password",
        )
        User.objects.filter(pk=self.user.pk).update(
            date_joined=timezone.now() - timedelta(days=3)
        )

    @patch("jobs.management.commands.send_drip_emails.email_service.send_email", return_value=True)
    def test_day_three_scout_email_has_tracked_scout_link(self, send_email):
        call_command("send_drip_emails", user=self.user.email)

        self.assertTrue(
            DripEmailLog.objects.filter(user=self.user, drip_type="day3_scout").exists()
        )
        self.assertEqual(send_email.call_count, 1)
        self.assertIn(
            "/alerts/?source=day3_scout_email", send_email.call_args.kwargs["html"]
        )

    @patch("jobs.management.commands.send_drip_emails.email_service.send_email")
    def test_day_three_scout_email_skips_users_with_an_existing_scout(self, send_email):
        JobAlert.objects.create(user=self.user, keywords="data analyst")

        call_command("send_drip_emails", user=self.user.email)

        send_email.assert_not_called()
        self.assertFalse(
            DripEmailLog.objects.filter(user=self.user, drip_type="day3_scout").exists()
        )
