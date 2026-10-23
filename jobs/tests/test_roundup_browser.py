import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import RoundupSubscriber
from jobs.services.roundup import active_subscribers


@override_settings(SECURE_SSL_REDIRECT=False, CSRF_COOKIE_SECURE=False, SESSION_COOKIE_SECURE=False)
class RoundupBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        cache.clear()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider, provider_id=provider_id, name=provider,
                client_id="test", secret="test",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(Site.objects.get_current())

    @patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"})
    def test_mobile_consent_confirmation_referral_and_unsubscribe(self):
        from playwright.sync_api import sync_playwright, expect

        folder = Path('.audit/free-acquisition-ship-2026-10-22')
        folder.mkdir(parents=True, exist_ok=True)
        referrer = RoundupSubscriber.objects.create(
            email='roundup-referrer@example.org', confirmed_at=timezone.now(),
        )
        with patch('jobs.views.roundup.email_service.send_email', return_value=True) as mail, sync_playwright() as p:
            executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
            chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            if not executable and Path(chrome).exists():
                executable = chrome
            browser = p.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
            context = browser.new_context(viewport={'width': 390, 'height': 844})
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.live_server_url) else route.abort())
            page = context.new_page()
            page.goto(self.live_server_url)
            page.get_by_role('link', name='Get the weekly job picks →', exact=True).click()
            self.assertEqual(page.locator('[name="source"]').input_value(), 'footer')
            page.goto(self.live_server_url + reverse('jobs:roundup') + f'?topic=climate&utm_source=partner_climate&utm_campaign=weekly_roundup&ref={referrer.referral_code}')
            self.assertEqual(page.locator('#roundup-topic').input_value(), 'climate')
            self.assertEqual(page.locator('[name="source"]').input_value(), 'partner_climate')
            self.assertEqual(page.locator('[name="campaign"]').input_value(), 'weekly_roundup')
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
            page.screenshot(path=str(folder/'newsletter-mobile-signup.png'), full_page=True)
            page.locator('#roundup-email').fill('roundup-browser@example.org')
            page.get_by_role('button', name='Send confirmation link', exact=True).click()
            self.assertEqual(mail.call_count, 0)
            self.assertEqual(RoundupSubscriber.objects.count(), 1)
            page.locator('[name="consent"]').check()
            page.get_by_role('button', name='Send confirmation link', exact=True).click()
            expect(page.get_by_role('status').filter(has_text='Check your inbox')).to_be_visible()
            page.reload()
            self.assertEqual(mail.call_count, 1)
            sub = RoundupSubscriber.objects.get(email='roundup-browser@example.org')
            self.assertEqual((sub.topic, sub.source, sub.campaign, sub.referred_by_id), ('climate', 'partner_climate', 'weekly_roundup', referrer.pk))
            self.assertIsNone(sub.confirmed_at)
            text = mail.call_args.kwargs['text']
            confirm_url = text.split('Confirm one weekly job roundup: ', 1)[1].split('\n', 1)[0]
            stop_url = mail.call_args.kwargs['unsubscribe_url']
            private_response = page.goto(self.live_server_url + urlsplit(confirm_url).path)
            self.assertEqual(private_response.headers['referrer-policy'], 'same-origin')
            self.assertEqual(page.locator('meta[name=referrer]').get_attribute('content'), 'same-origin')
            expect(page.get_by_role('button', name='Confirm my subscription')).to_be_visible()
            sub.refresh_from_db()
            self.assertIsNone(sub.confirmed_at)
            self.assertNotIn('googletagmanager.com', page.content())
            page.evaluate("() => { const link = document.createElement('a'); link.href='https://example.invalid/privacy-proof'; link.target='_blank'; link.textContent='Privacy probe'; document.body.append(link); }")
            with context.expect_event('request', predicate=lambda request: request.url == 'https://example.invalid/privacy-proof', timeout=5000) as external, context.expect_page() as probe:
                page.get_by_role('link', name='Privacy probe', exact=True).click()
            self.assertNotIn('referer', external.value.headers)
            probe.value.close()
            page.screenshot(path=str(folder/'newsletter-mobile-confirm.png'), full_page=True)
            with page.expect_response(lambda response: response.request.method == 'POST' and '/newsletter/confirm/' in response.url) as confirmation:
                page.get_by_role('button', name='Confirm my subscription').click()
            self.assertEqual(confirmation.value.status, 200)
            self.assertEqual(confirmation.value.request.headers.get('origin'), self.live_server_url)
            expect(page.get_by_role('heading', name="You're on the list")).to_be_visible()
            share = page.locator('#share-url').input_value()
            self.assertIn(str(sub.referral_code), share)
            self.assertNotIn(sub.email, share)
            self.assertEqual(active_subscribers().filter(referred_by=referrer).count(), 1)
            sub.refresh_from_db()
            confirmed = sub.confirmed_at
            page.reload()
            sub.refresh_from_db()
            self.assertEqual(sub.confirmed_at, confirmed)
            self.assertEqual(mail.call_count, 1)
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
            page.screenshot(path=str(folder/'newsletter-mobile-confirmed.png'), full_page=True)
            page.goto(self.live_server_url + urlsplit(stop_url).path)
            self.assertTrue(active_subscribers().filter(pk=sub.pk).exists())
            page.get_by_role('button', name='Unsubscribe from the roundup').click()
            expect(page.get_by_role('heading', name="You're unsubscribed")).to_be_visible()
            self.assertFalse(active_subscribers().filter(pk=sub.pk).exists())
            page.reload()
            self.assertFalse(active_subscribers().filter(pk=sub.pk).exists())
            self.assertEqual(mail.call_count, 1)
            page.goto(self.live_server_url + urlsplit(confirm_url).path)
            expect(page.get_by_role('heading', name='This link has expired')).to_be_visible()
            browser.close()
