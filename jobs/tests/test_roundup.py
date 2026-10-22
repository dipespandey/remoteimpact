import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from jobs.models import Job, NewsletterSubscriber, Organization, RoundupSubscriber, UserProfile
from jobs.services.roundup import active_subscribers, sample_jobs, token_for

@override_settings(SECURE_SSL_REDIRECT=False, SITE_URL="https://remoteimpact.org")
class RoundupTests(TestCase):
    def setUp(self):
        cache.clear()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider, client_id="test", secret="test", settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {})
            app.sites.add(Site.objects.get_current())
        self.url = reverse("jobs:roundup")
        self.payload = {"email": "reader@example.com", "consent": "yes", "topic": "health", "source": "partner", "campaign": "career_club"}
        self.send = patch("jobs.views.roundup.email_service.send_email", return_value=True).start()
        self.addCleanup(patch.stopall)
    def subscribe(self, **extra):
        return self.client.post(self.url, {**self.payload, **extra})
    def link(self, sub, purpose):
        return reverse("jobs:roundup_" + ("unsubscribe" if purpose == "unsubscribe" else "confirm"), args=[token_for(sub, purpose)])
    def test_public_landing_and_attribution(self):
        r=self.client.get(self.url+'?utm_source=partner')
        self.assertContains(r, 'href="https://remoteimpact.org/newsletter/"')
        self.assertContains(r, 'No account needed')
        self.assertIn('no-store',r['Cache-Control'])
        self.subscribe(); s=RoundupSubscriber.objects.get()
        self.assertEqual((s.source,s.campaign,s.topic),('partner','career_club','health'))
        self.assertIsNone(s.confirmed_at); self.assertEqual(active_subscribers().count(),0)
        self.assertEqual(NewsletterSubscriber.objects.count(),0)
        self.assertIn('/newsletter/confirm/',self.send.call_args.args[2])
    def test_scanner_get_is_not_confirmation_and_sensitive_page_has_no_analytics(self):
        self.subscribe(); s=RoundupSubscriber.objects.get(); link=self.link(s,'confirm')
        self.assertNotContains(self.client.get(link),'googletagmanager.com')
        s.refresh_from_db(); self.assertIsNone(s.confirmed_at)
        self.assertContains(self.client.post(link),"You're on the list")
        self.assertEqual(active_subscribers().count(),1)
    def test_csrf_and_invalid_token(self):
        self.subscribe(); s=RoundupSubscriber.objects.get()
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.link(s,'confirm')).status_code,403)
        self.assertEqual(self.client.post(reverse('jobs:roundup_confirm',args=['bad'])).status_code,400)
    def test_duplicate_and_active_preferences_protected(self):
        self.subscribe(); self.subscribe(email='READER@example.com')
        self.assertEqual(self.send.call_count,1); self.assertEqual(RoundupSubscriber.objects.count(),1)
        s=RoundupSubscriber.objects.get(); s.confirmed_at=timezone.now(); s.save()
        self.subscribe(topic='ai',source='attacker'); s.refresh_from_db()
        self.assertEqual((s.topic,s.source),('health','partner')); self.assertEqual(self.send.call_count,1)
    def test_confirmed_referral_credit(self):
        parent=RoundupSubscriber.objects.create(email='parent@example.com',confirmed_at=timezone.now())
        self.subscribe(ref=str(parent.referral_code)); s=RoundupSubscriber.objects.get(email='reader@example.com')
        self.assertEqual(s.referred_by_id,parent.pk)
        self.assertEqual(active_subscribers().filter(referred_by__isnull=False).count(),0)
        self.client.post(self.link(s,'confirm')); self.assertEqual(active_subscribers().filter(referred_by__isnull=False).count(),1)
    def test_unsubscribe_idempotent_and_resubscribe_needs_new_confirmation(self):
        self.subscribe(); s=RoundupSubscriber.objects.get(); confirm=self.link(s,'confirm'); stop=self.link(s,'unsubscribe')
        self.client.post(confirm); self.client.get(stop); self.assertEqual(active_subscribers().count(),1)
        self.assertEqual(self.client.post(stop).status_code,200); self.assertEqual(self.client.post(stop).status_code,200)
        self.assertEqual(active_subscribers().count(),0); self.assertEqual(self.client.post(confirm).status_code,400)
        s.refresh_from_db(); s.confirmation_requested_at=timezone.now()-timedelta(minutes=11); s.save()
        self.subscribe(); self.assertEqual(active_subscribers().count(),0)
        s.refresh_from_db(); self.client.post(self.link(s,'confirm')); self.assertEqual(active_subscribers().count(),1)
    def test_validation_honeypot_and_safe_labels(self):
        self.assertEqual(self.subscribe(consent='').status_code,400)
        self.assertEqual(self.subscribe(email='bad').status_code,400)
        self.subscribe(website='bot'); self.assertEqual(self.send.call_count,0)
        self.subscribe(source='private@example.com',campaign='https://example.com',topic='bad',ref='bad')
        s=RoundupSubscriber.objects.get(); self.assertEqual((s.source,s.campaign,s.topic),('newsletter','','all')); self.assertIsNone(s.referred_by)
    def test_ip_limit_and_mail_failure(self):
        self.send.return_value=False
        self.assertEqual(self.subscribe().status_code,503); self.assertEqual(active_subscribers().count(),0)
        for i in range(10): r=self.subscribe(email=f'p{i}@example.com')
        self.assertEqual(r.status_code,429); self.assertEqual(self.send.call_count,10)
    def test_registered_user_separate_preferences(self):
        u=get_user_model().objects.create_user(username='reader',email='reader@example.com')
        p,_=UserProfile.objects.get_or_create(user=u); p.email_newsletter=False; p.save()
        self.subscribe(); p.refresh_from_db(); self.assertFalse(p.email_newsletter); self.assertEqual(RoundupSubscriber.objects.count(),1)
    def test_private_export_only_confirmed_active_no_network(self):
        RoundupSubscriber.objects.create(email='active@example.com',confirmed_at=timezone.now())
        RoundupSubscriber.objects.create(email='pending@example.com')
        RoundupSubscriber.objects.create(email='stopped@example.com',confirmed_at=timezone.now(),unsubscribed_at=timezone.now())
        NewsletterSubscriber.objects.create(email='legacy@example.com',confirmed=True)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'export.json'; output=io.StringIO(); call_command('roundup_report',export=str(p),stdout=output)
            self.assertEqual([s['email'] for s in json.loads(p.read_text())],['active@example.com'])
            self.assertEqual(p.stat().st_mode&0o777,0o600); self.assertNotIn('active@example.com',output.getvalue())
            with self.assertRaises(CommandError): call_command('roundup_report',export=str(p),stdout=io.StringIO())
        self.assertEqual(self.send.call_count,0)
    def test_editorial_preview_deduplicates_and_excludes_closed(self):
        org=Organization.objects.create(name='Example',slug='example')
        for i in range(3): Job.objects.create(title=f'Role {i}',slug=f'role-{i}',organization=org,description='Useful substantive role. '*40,application_url='https://example.com/apply',quality_score=80,is_active=i!=2)
        self.assertEqual(len(sample_jobs()),1)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'draft'; call_command('prepare_roundup',output_dir=str(path),stdout=io.StringIO())
            manifest=json.loads((path/'review.json').read_text()); self.assertFalse(manifest['send_enabled']); self.assertIsNone(manifest['picks'][0]['employer_source_reviewed_at'])
        self.assertEqual(self.send.call_count,0)
    def test_manual_sender_requires_review_and_deduplicates_ambiguous_outcomes(self):
        from jobs.models import RoundupDelivery
        org=Organization.objects.create(name='Send Example',slug='send-example')
        Job.objects.create(title='Data Analyst',slug='data-analyst-send',organization=org,description='Useful substantive role. '*40,application_url='https://example.com/apply',quality_score=80)
        RoundupSubscriber.objects.create(email='confirmed@example.com',confirmed_at=timezone.now())
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            draft=Path(folder)/'draft'; call_command('prepare_roundup',output_dir=str(draft),stdout=io.StringIO())
            manifest=draft/'review.json'
            with override_settings(EMAIL_PROVIDER='resend'):
                with self.assertRaises(CommandError): call_command('send_roundup',manifest=str(manifest),send=True,stdout=io.StringIO())
                call_command('send_roundup',manifest=str(manifest),stdout=io.StringIO())
                self.assertEqual(RoundupDelivery.objects.count(),0)
                data=json.loads(manifest.read_text()); data['status']='approved'
                pick=data['picks'][0]; pick.update(employer_source_reviewed_at=timezone.now().isoformat(),eligible_countries_review='UK only',salary_review='Pay not stated by employer')
                manifest.write_text(json.dumps(data)); digest=hashlib.sha256(manifest.read_bytes()).hexdigest()
                with patch('jobs.management.commands.send_roundup.email_service._send_resend',return_value=False) as provider:
                    call_command('send_roundup',manifest=str(manifest),approve_sha256=digest,send=True,stdout=io.StringIO())
                    call_command('send_roundup',manifest=str(manifest),approve_sha256=digest,send=True,stdout=io.StringIO())
                    self.assertEqual(provider.call_count,1)
                    self.assertEqual(RoundupDelivery.objects.get().status,'unknown')
                    self.assertIn('List-Unsubscribe-Post',provider.call_args.args[-1])
    def test_expired_confirmation_is_rejected(self):
        from django.core.signing import TimestampSigner
        self.subscribe(); sub=RoundupSubscriber.objects.get()
        with patch('django.core.signing.time.time',return_value=timezone.now().timestamp()-3*86400):
            token=TimestampSigner(salt='roundup-confirm').sign(str(sub.confirmation_key))
        self.assertEqual(self.client.post(reverse('jobs:roundup_confirm',args=[token])).status_code,400)
    def test_partner_link_is_public_non_personal_and_does_not_send(self):
        output=io.StringIO();call_command('roundup_partner_link',partner='climate_club',topic='climate',stdout=output)
        self.assertIn('partner_climate_club',output.getvalue())
        with self.assertRaises(CommandError):call_command('roundup_partner_link',partner='private@example.com',stdout=io.StringIO())
        self.assertEqual(self.send.call_count,0)

    def test_confirmation_replay_does_not_inflate_new_confirmations(self):
        sub=RoundupSubscriber.objects.create(email='old@example.com',confirmed_at=timezone.now()-timedelta(days=10))
        original=sub.confirmed_at
        self.client.post(self.link(sub,'confirm'))
        sub.refresh_from_db(); self.assertEqual(sub.confirmed_at,original)
