import io
import re
from unittest.mock import patch

from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from jobs.models import NewsletterSubscriber, RoundupSubscriber, UserProfile
from jobs.services.roundup import active_subscribers


@override_settings(SECURE_SSL_REDIRECT=False, SITE_URL="https://remoteimpact.org")
class RoundupReconsentTests(TestCase):
    def setUp(self):
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider, client_id="test",
                                           secret="test", settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {})
            app.sites.add(Site.objects.get_current())
        self.send =patch("jobs.management.commands.send_roundup_reconsent.email_service.send_email",
                          return_value=True).start()
        self.addCleanup(patch.stopall)
        self.legacy = RoundupSubscriber.objects.create(email="legacy@example.com", consent_basis="legacy_account")
        self.unknown = RoundupSubscriber.objects.create(email="unknown@example.com", consent_basis="legacy_unknown")
        RoundupSubscriber.objects.create(email="weekly@example.com", confirmed_at=timezone.now())
        RoundupSubscriber.objects.create(email="monthly@example.com", consent_basis="monthly_only")
        RoundupSubscriber.objects.create(email="gone@example.com", consent_basis="legacy_account",
                                         unsubscribed_at=timezone.now())
        RoundupSubscriber.objects.create(email="footer-out@example.com", consent_basis="legacy_account")
        NewsletterSubscriber.objects.create(email="footer-out@example.com", unsubscribed=True)
        user = get_user_model().objects.create_user("acct", "Account-Out@example.com", "pw")
        UserProfile.objects.update_or_create(user=user, defaults={"email_newsletter_unsubscribed_at": timezone.now()})
        RoundupSubscriber.objects.create(email="account-out@example.com", consent_basis="legacy_account")
        self.send.reset_mock()

    def run_command(self, *args):
        out = io.StringIO()
        call_command("send_roundup_reconsent", *args, stdout=out)
        return out.getvalue()

    def recipients(self):
        return sorted(call.kwargs["to"] for call in self.send.call_args_list)

    def test_dry_run_lists_only_unconfirmed_legacy_members_and_sends_nothing(self):
        out = self.run_command()
        self.assertIn("Would email legacy@example.com", out)
        self.assertIn("Would email unknown@example.com", out)
        self.assertIn("Dry run: 2 of 2 eligible.", out)
        self.send.assert_not_called()

    def test_each_member_is_asked_once_across_runs(self):
        self.run_command("--send")
        self.run_command("--send")
        self.assertEqual(self.recipients(), ["legacy@example.com", "unknown@example.com"])

    def test_limit_leaves_the_rest_for_the_next_batch(self):
        self.assertIn("1 left for later batches", self.run_command("--send", "--limit", "1"))
        self.run_command("--send", "--limit", "1")
        self.assertEqual(self.recipients(), ["legacy@example.com", "unknown@example.com"])

    def test_failed_delivery_is_retried_next_run(self):
        self.send.return_value = False
        self.run_command("--send")
        self.send.return_value = True
        self.run_command("--send")
        self.assertEqual(self.send.call_count, 4)

    def test_confirm_link_in_the_email_subscribes_the_member(self):
        self.run_command("--send", "--limit", "1")
        text = self.send.call_args.kwargs["text"]
        confirm_path = re.search(r"https://remoteimpact\.org(/newsletter/confirm/[^\s]+/)", text).group(1)
        self.assertNotIn(self.legacy, active_subscribers())
        self.client.post(confirm_path)
        self.assertIn(self.legacy, active_subscribers())
