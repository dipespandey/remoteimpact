from django.test import TestCase
from django.urls import reverse
from django.core.cache import cache
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp


class PartnerResourcesTests(TestCase):
    def setUp(self):
        cache.clear()
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

    def test_partner_page_is_crawlable_and_exposes_feed_links(self):
        response = self.client.get(reverse("jobs:partner_resources"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Give your community a better way to find impact jobs.")
        self.assertContains(response, reverse("jobs:jobs_feed"))
        self.assertContains(response, reverse("jobs:category_landing", kwargs={"slug": "climate-environment"}))
        self.assertContains(response, reverse("jobs:job_scout_preview"))
        self.assertContains(response, "Copy blurb")
        self.assertContains(response, "partner_copy_blurb")
        self.assertContains(response, "utm_campaign=community_distribution")
        self.assertContains(response, 'name="robots" content="index, follow"')
        self.assertContains(response, 'rel="canonical"')
