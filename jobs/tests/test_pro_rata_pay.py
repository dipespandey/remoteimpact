from decimal import Decimal
from django.test import SimpleTestCase
from jobs.services.pro_rata_pay import calculate_pro_rata


class ProRataPayTests(SimpleTestCase):
    def test_part_time_fixed_term_advert(self):
        result = calculate_pro_rata(40000, 35, 21, 6)
        self.assertEqual(result['annual_gross'], Decimal('24000'))
        self.assertEqual(result['monthly_gross'], Decimal('2000'))
        self.assertEqual(result['contract_gross'], Decimal('12000'))

    def test_invalid_assumptions_are_rejected(self):
        for args in [(40000,0,21,6),(40000,35,40,6),('NaN',35,21,6),('Infinity',35,21,6),(40000,35,21,0)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                calculate_pro_rata(*args)

    def test_form_preserves_fractional_hours_and_rejects_actual_above_fte(self):
        from jobs.views.pro_rata import ProRataForm
        form = ProRataForm({'annual_fte':'40000','full_time_hours':'35','actual_hours':'17.5','months':'6'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.result['contract_gross'], Decimal('10000'))
        invalid = ProRataForm({'annual_fte':'40000','full_time_hours':'35','actual_hours':'40','months':'6'})
        self.assertFalse(invalid.is_valid())


from django.test import TestCase, override_settings
from django.urls import reverse
from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site


@override_settings(SECURE_SSL_REDIRECT=False)
class ProRataPageTests(TestCase):
    def setUp(self):
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider,
                client_id="test-client", secret="test-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {})
            app.sites.add(Site.objects.get_current())

    def test_public_page_and_result_with_private_post(self):
        url=reverse('jobs:pro_rata_pay')
        page=self.client.get(url)
        self.assertContains(page, 'No account needed.')
        self.assertContains(page, 'property="og:title" content="Pro Rata Salary Calculator: FTE to Actual Pay | Remote Impact"')
        self.assertContains(page, reverse('jobs:keyword_jobs',kwargs={'keyword_slug':'remote-part-time-jobs'}))
        response=self.client.post(url,{'annual_fte':'40000','full_time_hours':'35','actual_hours':'21','months':'6'})
        self.assertContains(response, '24000.00')
        self.assertContains(response, '12000.00')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Robots-Tag'], 'noindex, follow')
        self.assertNotIn('annual_fte', response.get('Location',''))

    def test_invalid_form_is_rendered_without_estimate(self):
        response=self.client.post(reverse('jobs:pro_rata_pay'),{'annual_fte':'40000','full_time_hours':'35','actual_hours':'40','months':'6'})
        self.assertContains(response, 'Actual hours must be positive')
        self.assertIsNone(response.context['result'])


from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from pathlib import Path
import os


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['*'])
class ProRataBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider,
                client_id="test-client", secret="test-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {})
            app.sites.add(Site.objects.get_current())

    def test_mobile_and_desktop_calculation(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            options = {'headless': True}
            if os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'):
                options['executable_path'] = os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE']
            browser = pw.chromium.launch(**options)
            try:
                for width in (390, 1440):
                    page = browser.new_page(viewport={'width':width,'height':1000})
                    page.goto(self.live_server_url + reverse('jobs:pro_rata_pay'))
                    page.get_by_label('Advertised annual full-time salary').fill('40000')
                    page.get_by_label('Paid contract length in months').fill('6')
                    page.get_by_role('button',name='Calculate actual pay').click()
                    page.get_by_text('24000.00',exact=True).wait_for()
                    self.assertTrue(page.get_by_text('12000.00',exact=True).is_visible())
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    folder = Path('.audit/pro-rata');folder.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(folder / f'pay-{width}.png'),full_page=True)
                    page.close()
            finally:
                browser.close()
