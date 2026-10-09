from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from allauth.socialaccount.models import SocialApp

from jobs.models import Category, Job, Organization


class ImpactJobsSnapshotTests(TestCase):
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

    def test_snapshot_is_crawlable_and_explains_methodology(self):
        response = self.client.get(reverse("jobs:impact_jobs_snapshot"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What does the remote impact jobs market look like?")
        self.assertContains(response, "How complete is the catalogue?")
        self.assertContains(response, "Remote does not always mean worldwide")
        self.assertContains(response, "Methodology:")
        self.assertContains(response, 'name="robots" content="index, follow"')
        self.assertContains(response, 'rel="canonical"')

    def test_snapshot_separates_global_named_and_unclear_scope(self):
        category = Category.objects.create(name="Climate", slug="climate-test")
        organization = Organization.objects.create(name="Test Org", slug="test-org")
        common = {
            "organization": organization,
            "category": category,
            "description": "Test description",
            "requirements": "Test requirements",
        }
        Job.objects.create(
            **common,
            title="Global role",
            slug="global-role",
            location="Remote - Worldwide",
            country="Worldwide",
        )
        Job.objects.create(
            **common,
            title="Country role",
            slug="country-role",
            location="Remote (Germany)",
            country="Germany",
        )
        Job.objects.create(
            **common,
            title="Unclear role",
            slug="unclear-role",
            location="Remote",
            country="",
        )

        response = self.client.get(reverse("jobs:impact_jobs_snapshot"))

        self.assertEqual(response.context["remote_scope"]["explicitly_global"]["count"], 1)
        self.assertEqual(response.context["remote_scope"]["named_geography"]["count"], 1)
        self.assertEqual(response.context["remote_scope"]["unclear"]["count"], 1)
        self.assertContains(response, "Find roles open worldwide")
