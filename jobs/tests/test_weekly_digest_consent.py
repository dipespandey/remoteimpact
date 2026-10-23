"""The legacy command must obey the canonical weekly consent boundary."""
import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from jobs.models import RoundupSubscriber, UserProfile


class WeeklyDigestConsentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('reader', 'reader@example.org', 'pw-test-123')

    def run_digest(self):
        output = StringIO()
        call_command('send_weekly_digest', dry_run=True, stdout=output)
        return json.loads(output.getvalue())

    def test_legacy_checkbox_is_not_explicit_weekly_consent(self):
        UserProfile.objects.create(user=self.user, email_newsletter=True)
        self.assertEqual(self.run_digest()['confirmed_weekly'], 0)

    def test_confirmed_weekly_subscriber_without_completed_profile_is_included(self):
        RoundupSubscriber.objects.create(email=self.user.email, confirmed_at=timezone.now())
        result = self.run_digest()
        self.assertEqual(result['confirmed_weekly'], 1)
        self.assertFalse(result['requires_completed_profile'])
        self.assertEqual(result['emails_sent'], 0)

    def test_unsubscribed_user_is_excluded_even_if_legacy_preference_was_true(self):
        UserProfile.objects.create(user=self.user, email_newsletter=True)
        RoundupSubscriber.objects.create(email=self.user.email, confirmed_at=timezone.now(), unsubscribed_at=timezone.now())
        self.assertEqual(self.run_digest()['confirmed_weekly'], 0)

    def test_recipient_override_cannot_bypass_canonical_audience(self):
        with self.assertRaises(CommandError):
            call_command('send_weekly_digest', user=self.user.email, dry_run=True, stdout=StringIO())
