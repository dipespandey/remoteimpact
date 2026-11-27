from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import JobAlert, RoundupSubscriber
from jobs.services.roundup import active_subscribers


@override_settings(SECURE_SSL_REDIRECT=False, SITE_URL="https://remoteimpact.org")
class ScoutResultsRoundupOfferTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("reader", "reader@example.org", "pw")
        EmailAddress.objects.create(user=self.user, email=self.user.email, verified=True, primary=True)
        self.client.force_login(self.user)
        self.alert = JobAlert.objects.create(user=self.user, intent_text="Climate policy roles", frequency="once")
        self.page = reverse("jobs:job_alerts") + f"?created={self.alert.pk}"

    def test_results_page_offers_the_roundup_and_one_click_subscribes(self):
        self.assertContains(self.client.get(self.page), "Send me the weekly picks")
        with patch("jobs.views.roundup.email_service.send_email") as send:
            response = self.client.post(reverse("jobs:newsletter_preferences"),
                                        {"subscribe": "true", "source": "scout_results", "next": self.page})
        self.assertRedirects(response, self.page, fetch_redirect_response=False)
        sub = active_subscribers().get(email="reader@example.org")
        self.assertEqual(sub.source, "scout_results")
        send.assert_not_called()
        self.assertNotContains(self.client.get(self.page), "Send me the weekly picks")

    def test_offsite_next_falls_back_to_the_account_page(self):
        response = self.client.post(reverse("jobs:newsletter_preferences"),
                                    {"subscribe": "true", "next": "https://evil.example/"})
        self.assertRedirects(response, reverse("jobs:account"), fetch_redirect_response=False)


@override_settings(SITE_URL="https://remoteimpact.org")
class ConfirmReminderTests(TestCase):
    def request(self, email, hours_ago, **extra):
        return RoundupSubscriber.objects.create(
            email=email, consent_basis="weekly_request",
            confirmation_requested_at=timezone.now() - timedelta(hours=hours_ago), **extra)

    def run_send(self):
        with patch("jobs.management.commands.send_roundup_confirm_reminders.email_service.send_email",
                   return_value=True) as send:
            call_command("send_roundup_confirm_reminders", "--send", stdout=StringIO())
        return [call.kwargs["to"] for call in send.call_args_list]

    def test_reminds_each_unconfirmed_request_once_after_a_day(self):
        self.request("due@example.org", 30)
        self.request("fresh@example.org", 2)
        self.request("stale@example.org", 24 * 10)
        self.request("done@example.org", 30, confirmed_at=timezone.now())
        self.request("stopped@example.org", 30, unsubscribed_at=timezone.now())
        RoundupSubscriber.objects.create(email="legacy@example.org", consent_basis="legacy_account",
                                         confirmation_requested_at=timezone.now() - timedelta(hours=30))
        self.assertEqual(self.run_send(), ["due@example.org"])
        self.assertEqual(self.run_send(), [])

    def test_dry_run_sends_nothing(self):
        self.request("due@example.org", 30)
        with patch("jobs.management.commands.send_roundup_confirm_reminders.email_service.send_email") as send:
            out = StringIO()
            call_command("send_roundup_confirm_reminders", stdout=out)
        send.assert_not_called()
        self.assertIn("Dry run: 1 eligible.", out.getvalue())

    def test_the_reminder_carries_a_fresh_confirm_link(self):
        from jobs.services.roundup import subscriber_for
        row = self.request("due@example.org", 30)
        with patch("jobs.management.commands.send_roundup_confirm_reminders.email_service.send_email",
                   return_value=True) as send:
            call_command("send_roundup_confirm_reminders", "--send", stdout=StringIO())
        link = send.call_args.kwargs["html"].split('/newsletter/confirm/')[1].split('/"')[0]
        self.assertEqual(subscriber_for(link, "confirm"), row)
