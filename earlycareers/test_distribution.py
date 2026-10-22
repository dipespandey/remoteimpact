from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse

from earlycareers.models import DirectoryEvent, ProgrammeBookmark


class PublicDistributionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider, provider_id=provider_id, name=provider,
                client_id="test-client", secret="test-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(site)

    def test_only_public_embed_allows_framing(self):
        response = self.client.get(reverse("jobs:early_careers_embed"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("frame-ancestors *;", response["Content-Security-Policy"])
        self.assertNotIn("X-Frame-Options", response)
        report = self.client.get(reverse("jobs:early_careers_access_report"))
        self.assertEqual(report.status_code, 200)
        self.assertIn("frame-ancestors 'none';", report["Content-Security-Policy"])
        self.assertEqual(report["X-Frame-Options"], "DENY")

    def test_embed_does_not_expose_or_mutate_private_shortlist(self):
        user = get_user_model().objects.create_user(
            username="private-directory-user", email="private-directory@example.com",
        )
        ProgrammeBookmark.objects.create(user=user, slug="unicef")
        self.client.force_login(user)
        response = self.client.get(reverse("jobs:early_careers_embed"))
        self.assertNotContains(response, user.email)
        self.assertNotContains(response, user.username)
        self.assertNotContains(response, "Saved · remove")
        self.assertLessEqual(len(response.context["items"]), 6)
        self.assertTrue(all(item["status"] != "closed" for item in response.context["items"]))
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertContains(response, 'name="robots" content="noindex, nofollow"')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(ProgrammeBookmark.objects.count(), 1)
        self.assertEqual(DirectoryEvent.objects.count(), 0)

    def test_distribution_pages_reject_posts(self):
        for name in ("early_careers_embed", "early_careers_access_report"):
            self.assertEqual(self.client.post(reverse("jobs:" + name)).status_code, 405)
        self.assertFalse(ProgrammeBookmark.objects.exists())

    def test_trust_pages_use_company_contact(self):
        for name in ("about", "contact"):
            response = self.client.get(reverse("jobs:" + name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Remote Impact")
        self.assertContains(response, "hello@remoteimpact.org")
