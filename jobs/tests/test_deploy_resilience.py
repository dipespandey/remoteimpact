from unittest.mock import patch

import requests
from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse

from jobs.sitemaps import GuidesSitemap
from jobs.views.resources import CLUSTER_MAP, guide_template_exists


class SocialAppsMixin:
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


class GuidePageTests(SocialAppsMixin, TestCase):
    def test_unwritten_cluster_guide_is_404_not_500(self):
        missing = [key for key, template in CLUSTER_MAP.items() if not guide_template_exists(template)]
        if not missing:
            self.skipTest("every mapped guide has a template")
        pillar_slug, cluster_slug = missing[0]
        url = reverse("jobs:cluster_detail", kwargs={"pillar_slug": pillar_slug, "cluster_slug": cluster_slug})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_sitemap_only_lists_written_guides(self):
        for kind, pillar_slug, cluster_slug, _ in GuidesSitemap().items():
            if kind == "cluster":
                self.assertTrue(guide_template_exists(CLUSTER_MAP[(pillar_slug, cluster_slug)]))


class SocialLoginNetworkErrorTests(SocialAppsMixin, TestCase):
    def test_unreachable_provider_redirects_to_login(self):
        with patch(
            "requests.Session.request",
            side_effect=requests.ConnectionError("Network is unreachable"),
        ):
            response = self.client.get("/accounts/oidc/linkedin/login/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("account_login"))
