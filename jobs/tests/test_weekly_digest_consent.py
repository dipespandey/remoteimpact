from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from jobs.models import UserProfile


class WeeklyDigestConsentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "reader", "reader@example.org", "pw-test-123"
        )

    def run_digest(self):
        output = StringIO()
        call_command(
            "send_weekly_digest",
            "--user",
            self.user.email,
            "--dry-run",
            stdout=output,
        )
        return output.getvalue()

    def test_specific_user_requires_explicit_newsletter_consent(self):
        output = self.run_digest()
        self.assertIn("No active, newsletter-subscribed account found", output)

    def test_specific_user_with_consent_is_included(self):
        UserProfile.objects.create(user=self.user, email_newsletter=True)
        output = self.run_digest()
        self.assertIn("Found 1 users to send digests to", output)

    def test_unsubscribed_user_is_excluded_even_if_preference_was_true(self):
        from django.utils import timezone

        UserProfile.objects.create(
            user=self.user,
            email_newsletter=True,
            email_newsletter_unsubscribed_at=timezone.now(),
        )
        output = self.run_digest()
        self.assertIn("No active, newsletter-subscribed account found", output)
