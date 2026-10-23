"""Consent boundaries and duplicate-free delivery, without network operations."""
import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.signing import TimestampSigner
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.models import NewsletterSubscriber, RoundupSubscriber, UserProfile
from jobs.services.newsletter_audience import reconcile_audience, subscribe_account, unsubscribe_newsletters
from jobs.services.roundup import active_subscribers, token_for


class NewsletterAudienceTests(TestCase):
    def setUp(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        from django.core.cache import cache
        cache.clear()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider,
                client_id="test", secret="test", settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {})
            app.sites.add(Site.objects.get_current())

    def user(self, email='reader@example.org'):
        user = get_user_model().objects.create_user(username=email, email=email, password='test-password')
        from allauth.account.models import EmailAddress
        EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.email_newsletter = True
        profile.save()
        return user

    def test_legacy_checkbox_and_monthly_confirmation_never_become_weekly_consent(self):
        self.user()
        NewsletterSubscriber.objects.create(email='READER@example.org', confirmed=True)
        NewsletterSubscriber.objects.create(email='monthly@example.org', confirmed=True)
        with patch('jobs.services.email_service.email_service.send_email') as send:
            before = reconcile_audience()
            self.assertEqual(RoundupSubscriber.objects.count(), 2)
            applied = reconcile_audience(apply=True)
            repeated = reconcile_audience(apply=True)
            send.assert_not_called()
        self.assertEqual(before['new_records'], 0)
        self.assertEqual(applied['weekly_consent_granted'], 0)
        self.assertEqual(repeated['new_records'], 0)
        self.assertEqual(repeated['updated_records'], 0)
        self.assertEqual(active_subscribers().count(), 0)
        self.assertEqual(RoundupSubscriber.objects.get(email='reader@example.org').legacy_sources,
                         ['account_preference', 'monthly_product_updates'])
        self.assertEqual(RoundupSubscriber.objects.get(email='monthly@example.org').audience_status, 'Monthly updates only')

    def test_newer_explicit_account_consent_needs_no_seeker_profile_and_wins_old_optout(self):
        user = self.user()
        old = timezone.now() - timedelta(days=2)
        NewsletterSubscriber.objects.create(email=user.email, confirmed=True, unsubscribed=True, unsubscribed_at=old)
        reconcile_audience(apply=True)
        self.assertEqual(active_subscribers().count(), 0)
        sub = subscribe_account(user)
        reconcile_audience(apply=True)
        self.assertEqual(active_subscribers().get().pk, sub.pk)
        self.assertFalse(hasattr(user, 'seeker_profile'))
        output = io.StringIO()
        call_command('send_weekly_digest', dry_run=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['confirmed_weekly'], 1)

    def test_unsubscribe_propagates_across_all_newsletter_lists(self):
        user = self.user()
        NewsletterSubscriber.objects.create(email=user.email.upper(), confirmed=True)
        sub = subscribe_account(user)
        token = token_for(sub, 'unsubscribe')
        response = self.client.post(reverse('jobs:roundup_unsubscribe', args=[token]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(active_subscribers().count(), 0)
        user.profile.refresh_from_db()
        self.assertFalse(user.profile.email_newsletter)
        self.assertIsNotNone(user.profile.email_newsletter_unsubscribed_at)
        self.assertTrue(NewsletterSubscriber.objects.get().unsubscribed)
        reconcile_audience(apply=True)
        self.assertEqual(active_subscribers().count(), 0)

    def test_old_unsubscribe_links_also_stop_new_weekly_audience(self):
        user = self.user()
        subscribe_account(user)
        token = TimestampSigner(salt='newsletter-unsubscribe').sign(str(user.pk))
        self.client.post(reverse('jobs:newsletter_unsubscribe', args=[token]))
        self.assertFalse(active_subscribers().exists())
        subscribe_account(user)
        token = TimestampSigner(salt='subscriber-unsubscribe').sign(user.email)
        self.client.post(reverse('jobs:newsletter_unsubscribe', args=[token]))
        self.assertFalse(active_subscribers().exists())

    def test_dashboard_action_records_explicit_consent_and_optout(self):
        user = self.user()
        self.client.force_login(user)
        self.client.post(reverse('jobs:newsletter_preferences'), {'subscribe': 'true'})
        self.assertEqual(active_subscribers().get().consent_basis, 'account_explicit')
        self.client.post(reverse('jobs:newsletter_preferences'), {'subscribe': 'false'})
        self.assertFalse(active_subscribers().exists())

    def test_public_weekly_confirmation_upgrades_imported_record_only_after_post(self):
        user = self.user()
        reconcile_audience(apply=True)
        sub = RoundupSubscriber.objects.get()
        old = token_for(sub, 'confirm')
        unsubscribe_newsletters(user.email)
        self.assertEqual(self.client.post(reverse('jobs:roundup_confirm', args=[old])).status_code, 400)
        with patch('jobs.views.roundup.email_service.send_email', return_value=True):
            self.client.post(reverse('jobs:roundup'), {'email':user.email, 'consent':'yes'})
        sub.refresh_from_db()
        link = reverse('jobs:roundup_confirm', args=[token_for(sub, 'confirm')])
        self.client.get(link)
        self.assertFalse(active_subscribers().exists())
        self.client.post(link)
        self.assertEqual(active_subscribers().get().consent_basis, 'weekly_request')
        reconcile_audience(apply=True)
        self.assertEqual(active_subscribers().count(), 1)

    def test_newer_legacy_optout_is_reconciled_before_broadcast(self):
        user = self.user()
        subscribe_account(user)
        NewsletterSubscriber.objects.create(email=user.email, unsubscribed=True, unsubscribed_at=timezone.now())
        reconcile_audience(apply=True)
        self.assertFalse(active_subscribers().exists())

    def test_invalid_legacy_email_is_skipped_and_not_exposed(self):
        NewsletterSubscriber.objects.create(email='invalid-email')
        output = io.StringIO()
        call_command('reconcile_newsletter_audience', apply=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['invalid_records'], 1)
        self.assertNotIn('invalid-email', output.getvalue())

    def test_unverified_account_requests_confirmation_instead_of_becoming_active(self):
        from allauth.account.models import EmailAddress
        user = self.user()
        EmailAddress.objects.filter(user=user).update(verified=False)
        self.client.force_login(user)
        with patch('jobs.views.roundup.email_service.send_email', return_value=True) as send:
            response = self.client.post(reverse('jobs:newsletter_preferences'), {'subscribe':'true'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(send.call_count, 1)
        self.assertFalse(active_subscribers().exists())
        self.assertIsNone(RoundupSubscriber.objects.get().confirmed_at)

    def test_topic_preference_is_respected_and_monthly_only_is_not_sent(self):
        import hashlib
        import tempfile
        from pathlib import Path
        from django.test import override_settings
        from jobs.models import Category, Job, Organization, RoundupDelivery
        from jobs.services.roundup import issue_week
        category = Category.objects.create(name='Climate', slug='climate-environment')
        org = Organization.objects.create(name='Example', slug='example-topic')
        job = Job.objects.create(title='Analyst', slug='analyst-topic', organization=org, category=category,
            description='Substantive work. '*50, application_url='https://example.org/apply', quality_score=80)
        now = timezone.now()
        RoundupSubscriber.objects.create(email='health@example.org', topic='health', confirmed_at=now)
        RoundupSubscriber.objects.create(email='all@example.org', topic='all', confirmed_at=now)
        NewsletterSubscriber.objects.create(email='monthly@example.org', confirmed=True)
        data = {'issue_week':issue_week(), 'status':'approved', 'picks':[{
            'job_id':str(job.pk), 'title':job.title, 'employer':org.name, 'employer_source':job.application_url,
            'url':'https://remoteimpact.org'+job.get_absolute_url(), 'employer_source_reviewed_at':now.isoformat(),
            'eligible_countries_review':'UK only', 'salary_review':'Pay not stated'}]}
        with tempfile.TemporaryDirectory() as folder, override_settings(EMAIL_PROVIDER='resend', SITE_URL='https://remoteimpact.org'):
            path = Path(folder)/'issue.json'; path.write_text(json.dumps(data))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch('jobs.management.commands.send_roundup.email_service._send_resend', return_value=True) as send:
                call_command('send_roundup', manifest=str(path), approve_sha256=digest, send=True, stdout=io.StringIO())
                call_command('send_roundup', manifest=str(path), approve_sha256=digest, send=True, stdout=io.StringIO())
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[0], 'all@example.org')
            self.assertEqual(RoundupDelivery.objects.count(), 1)

    def test_old_release_can_insert_with_database_defaults_after_migration(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        apps = MigrationExecutor(connection).loader.project_state([('jobs', '0085_scout_preview_tasks')]).apps
        OldSubscriber = apps.get_model('jobs', 'RoundupSubscriber')
        old = OldSubscriber.objects.create(email='old-release@example.org')
        current = RoundupSubscriber.objects.get(pk=old.pk)
        self.assertEqual(current.consent_basis, 'weekly_request')
        self.assertEqual(current.legacy_sources, [])
        self.assertFalse(active_subscribers().filter(pk=current.pk).exists())
