from datetime import date, datetime, timedelta, timezone as dt_timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import json

from allauth.account.signals import user_signed_up
from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from io import StringIO

from jobs.models import ConsumerFunnelEvent, UserProfile
from jobs.services.early_careers import opportunities
from .models import DirectoryEvent, ProgrammeBookmark
from .services import PENDING_KEY, INTENT_KEY


class DirectoryConversionTests(TestCase):
    def setUp(self):
        self.mail = patch('jobs.services.email_service.email_service.send_email').start()
        self.addCleanup(patch.stopall)
        self.client.defaults['HTTP_USER_AGENT'] = 'Mozilla/5.0 DirectoryTest'
        site = Site.objects.get_current()
        for provider, provider_id in [('google',''), ('openid_connect','linkedin')]:
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider, client_id='test', secret='test', settings={'server_url':'https://www.linkedin.com/oauth'} if provider_id else {})
            app.sites.add(site)
        self.user = get_user_model().objects.create_user(username='seeker', email='seeker@example.com', password='T3st-application-912!')
        self.other = get_user_model().objects.create_user(username='other', email='other@example.com')
        self.hub = reverse('jobs:early_careers')
        self.save = reverse('jobs:early_careers_save', args=['unicef'])

    def test_guest_save_keeps_intent_and_contextual_signup(self):
        response = self.client.post(self.save, {'next':self.hub+'?funding=paid#unicef'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('account_signup')))
        self.assertEqual(self.client.session[PENDING_KEY], ['unicef'])
        signup = self.client.get(response.url)
        self.assertContains(signup, 'Keep your next steps together')
        self.assertContains(signup, 'Continue browsing without an account')
        self.assertContains(signup, 'early_careers_signup_submit')
        self.assertFalse(ProgrammeBookmark.objects.exists())
        self.assertEqual(DirectoryEvent.objects.filter(event='signup_start').count(), 1)

    def test_existing_account_login_restores_pending_save_without_counting_signup(self):
        self.client.post(self.save)
        self.client.login(username='seeker', password='T3st-application-912!')
        self.assertTrue(ProgrammeBookmark.objects.filter(user=self.user, slug='unicef').exists())
        self.assertNotIn(PENDING_KEY, self.client.session)
        self.assertFalse(DirectoryEvent.objects.filter(event='signup_complete').exists())

    def test_real_signup_preserves_programme_and_campaign(self):
        self.client.get(self.hub, {'utm_source':'test_campaign','utm_medium':'paid_social'})
        handoff = self.client.post(self.save)
        signup_url = handoff.url
        response = self.client.post(signup_url, {'email':'new-reader@example.com', 'password1':'N3w-reader-48291!', 'password2':'N3w-reader-48291!', 'next':parse_qs(urlsplit(signup_url).query)['next'][0]})
        self.assertEqual(response.status_code, 302, response.content[:1000])
        user = get_user_model().objects.get(email='new-reader@example.com')
        self.assertTrue(ProgrammeBookmark.objects.filter(user=user, slug='unicef').exists())
        self.assertTrue(response.url.startswith(self.hub))
        event = DirectoryEvent.objects.get(event='signup_complete', user=user)
        self.assertEqual(event.metadata['utm_source'], 'test_campaign')
        self.assertEqual(ConsumerFunnelEvent.objects.get(user=user, event='account_created').metadata['entry_point'], 'early_careers')
        self.assertFalse(UserProfile.objects.filter(user=user, email_newsletter=True).exists())

    def test_signup_failure_retains_selection_for_retry(self):
        handoff = self.client.post(self.save)
        response = self.client.post(handoff.url, {'email':'bad-email', 'password1':'short', 'password2':'different'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session[PENDING_KEY], ['unicef'])
        self.assertFalse(DirectoryEvent.objects.filter(event='signup_complete').exists())

    def test_saves_are_idempotent_and_private_across_accounts(self):
        self.client.force_login(self.user)
        self.client.post(self.save); self.client.post(self.save)
        self.assertEqual(ProgrammeBookmark.objects.filter(user=self.user).count(), 1)
        self.assertEqual(DirectoryEvent.objects.filter(event='save').count(), 1)
        page = self.client.get(self.hub, {'saved':'1'})
        self.assertEqual(page.context['result_count'], 1)
        self.assertContains(page, 'noindex, follow')
        self.assertIn('no-store', page['Cache-Control'])
        self.client.force_login(self.other)
        other = self.client.get(self.hub, {'saved':'1'})
        self.assertEqual(other.context['result_count'], 0)
        self.assertNotContains(other, '✓ Saved · remove')
        self.client.post(self.save, {'action':'remove'})
        self.assertTrue(ProgrammeBookmark.objects.filter(user=self.user, slug='unicef').exists())

    def test_applied_state_can_be_reset_and_removed(self):
        self.client.force_login(self.user)
        self.client.post(self.save)
        self.client.post(self.save, {'action':'applied'})
        self.assertEqual(ProgrammeBookmark.objects.get(user=self.user).status, 'applied')
        self.assertContains(self.client.get(self.hub, {'saved':'1'}), 'Marked as applied')
        self.client.post(self.save, {'action':'reset'})
        self.assertEqual(ProgrammeBookmark.objects.get(user=self.user).status, 'saved')
        self.client.post(self.save, {'action':'remove'})
        self.assertFalse(ProgrammeBookmark.objects.exists())

    def test_save_requires_csrf_and_never_mutates_on_get(self):
        secure = Client(enforce_csrf_checks=True)
        self.assertEqual(secure.post(self.save).status_code, 403)
        self.assertEqual(self.client.get(self.save).status_code, 405)
        self.assertNotIn(PENDING_KEY, self.client.session)

    def test_invalid_programmes_and_actions_are_rejected(self):
        self.assertEqual(self.client.post(reverse('jobs:early_careers_save',args=['unknown'])).status_code, 404)
        self.assertEqual(self.client.post(self.save, {'action':'subscribe'}).status_code, 400)
        self.assertEqual(self.client.post(self.save, {'action':'remove'}).status_code, 400)

    def test_return_destinations_stay_internal(self):
        self.client.force_login(self.user)
        for target in ['https://evil.example/', '//evil.example/', '/accounts/logout/', 'https://[invalid', self.hub+'\\evil']:
            response = self.client.post(self.save, {'next':target})
            self.assertEqual(response.url, self.hub)

    def test_join_does_not_make_an_empty_bookmark(self):
        handoff = self.client.get(reverse('jobs:early_careers_join'), {'placement':'hero'})
        self.assertEqual(handoff.status_code, 302)
        signup = self.client.get(handoff.url)
        self.assertNotContains(signup, 'Your selected programme will be saved')
        self.client.login(username='seeker', password='T3st-application-912!')
        self.assertFalse(ProgrammeBookmark.objects.exists())

    def test_stale_selection_is_not_silently_saved(self):
        self.client.post(self.save)
        session = self.client.session
        session[INTENT_KEY]['at'] = (timezone.now()-timedelta(days=2)).isoformat()
        session.save()
        self.client.login(username='seeker', password='T3st-application-912!')
        self.assertFalse(ProgrammeBookmark.objects.exists())

    def test_source_open_is_free_and_fixed_to_registry_destination(self):
        url = reverse('jobs:early_careers_outbound',args=['unfpa-internships'])
        response = self.client.get(url, {'next':'https://evil.example/'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('https://www.unfpa.org/jobs'))
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertEqual(DirectoryEvent.objects.filter(event='outbound').count(), 1)
        self.assertFalse(ProgrammeBookmark.objects.exists())

    def test_known_bots_do_not_count_as_prospective_signups(self):
        self.client.get(self.hub, HTTP_USER_AGENT='Googlebot')
        self.client.get(reverse('jobs:early_careers_join'), HTTP_USER_AGENT='Googlebot')
        self.assertFalse(DirectoryEvent.objects.exists())

    def test_curated_pay_and_deadline_match_current_evidence(self):
        rows = {r['slug']:r for r in opportunities(date(2026,10,19))}
        self.assertEqual(rows['edf-climate-corps']['funding'], 'paid')
        self.assertEqual(rows['edf-climate-corps']['status'], 'open')
        self.assertEqual(rows['edf-climate-corps']['deadline'], '2026-12-20')
        self.assertIn('1,400',rows['edf-climate-corps']['funding_detail'])
        self.assertIn('21–30',rows['fao-internships']['requirements'])
        self.assertTrue(all(row.get('eligibility_summary') for row in rows.values()))

    def test_funnel_report_is_aggregate_and_does_not_include_emails(self):
        self.client.get(reverse('jobs:early_careers_join'))
        output = StringIO(); call_command('early_careers_report', stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report['events']['signup_start'], 1)
        self.assertNotIn('example.com',output.getvalue())

    def test_deadline_uses_provider_timezone_at_midnight(self):
        row = dict(opportunities(date(2026,10,19))[0], status='open', deadline='2026-12-20', deadline_timezone='America/Los_Angeles', checked_on='2026-12-20')
        with patch('jobs.services.early_careers.registry',return_value=[row]):
            with patch('jobs.services.early_careers.timezone.now',return_value=datetime(2026,12,21,0,0,tzinfo=dt_timezone.utc)):
                self.assertEqual(opportunities()[0]['status'], 'open')
            with patch('jobs.services.early_careers.timezone.now',return_value=datetime(2026,12,21,8,1,tzinfo=dt_timezone.utc)):
                self.assertEqual(opportunities()[0]['status'], 'closed')
