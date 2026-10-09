from django.test import TestCase
from django.urls import reverse
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp


class PublicPricingTests(TestCase):
    def setUp(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider,
                provider_id=provider_id,
                name=(provider_id or provider).title(),
                client_id=f"{provider_id or provider}-client",
                secret=f"{provider_id or provider}-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(site)

    def test_pricing_page_is_public_and_describes_free_and_pro_offers(self):
        response = self.client.get(reverse("jobs:public_pricing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remote Impact Pro")
        self.assertContains(response, "$10")
        self.assertContains(response, "one AI Job Scout")
        self.assertContains(response, "pricing_free_scout")
        self.assertContains(response, 'name="robots" content="index, follow"')
        self.assertContains(response, 'rel="canonical"')
