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
