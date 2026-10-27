import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from jobs.models import Job, Organization
from jobs.services.nonprofit_hiring import build_snapshot, get_snapshot, location_label
from jobs.views.hiring_evidence import HiringEvidenceSitemap


@override_settings(SITE_URL="https://remoteimpact.org", INDEXNOW_ENABLED=False,
                   PUBLIC_PAGE_CACHE_ENABLED=False)
class NonprofitHiringTests(TestCase):
    def setUp(self):
        cache.clear()
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider,
                                           client_id="synthetic", secret="synthetic")
            app.sites.add(Site.objects.get_current())
        self.embedding = patch("jobs.services.embedding_service.embed_job", return_value=None)
        self.embedding.start()
        self.addCleanup(self.embedding.stop)
        self.addCleanup(cache.clear)
        self.org = Organization.objects.create(name="Example Charity", slug="example-charity", organization_type="nonprofit")
        self.counter = 0

    def job(self, **kwargs):
        self.counter += 1
        return Job.objects.create(organization=kwargs.pop("organization", self.org),
                                  title="Research assistant", slug=f"np-evidence-{self.counter}",
                                  source=kwargs.pop("source", "manual"), **kwargs)

    def test_groups_are_exclusive_missing_not_global_and_labels_not_eligibility(self):
        self.job(country="USA", location="Remote (US)")
        self.job(country="United States", location="Remote (US)")
        self.job(country="Worldwide", requirements="Only residents of Canada may apply.")
        self.job(country="", location="Remote in Germany")
        self.job(country="Europe")
        self.job(country="US, Canada")
        self.job(country="USA", is_active=False)
        data = build_snapshot()
        self.assertEqual(data["total_listings"], 6)
        groups = {row["key"]: row["listings"] for row in data["location_groups"]}
        self.assertEqual(groups, {"country": 2, "worldwide": 1, "other": 2, "unknown": 1})
        self.assertEqual(sum(groups.values()), data["total_listings"])
        self.assertEqual(sum(data["classification_basis"].values()), data["total_listings"])
        self.assertEqual(data["total_employers"], 1)
        self.assertEqual(next(row["listings"] for row in data["location_labels"] if row["group"] == "country"), 2)
        self.assertIn("does not prove unrestricted hiring", " ".join(data["methodology"]))

    def test_classification_scope_matches_destination(self):
        source = Organization.objects.create(name="Source Example", slug="source-example")
        name = Organization.objects.create(name="Discovery Foundation", slug="discovery-foundation")
        excluded = Organization.objects.create(name="Commercial Foundation", slug="commercial-foundation", organization_type="social_enterprise")
        self.job(country="USA")
        self.job(organization=source, source="idealist", raw_data={"orgType": "NONPROFIT"})
        self.job(organization=name)
        self.job(organization=excluded)
        self.job(organization=source, source="idealist", raw_data={"orgType": "FOR_PROFIT"})
        data = build_snapshot()
        self.assertEqual(data["total_listings"], 3)
        self.assertEqual(data["classification_basis"], {"recorded_type": 1, "source_classification": 1, "name_candidate": 1})

    def test_ambiguous_labels_not_expanded_into_country_eligibility(self):
        for label in ("Europe", "US and Canada", "Remote", "Et", "Md", "global except US"):
            self.assertEqual(location_label(label)[0], "other", label)
        self.assertEqual(location_label("ET")[0], "country")
        self.assertEqual(location_label("Worldwide")[0], "worldwide")
        self.assertEqual(location_label("   ")[0], "unknown")

    def test_page_json_share_snapshot_and_have_citable_metadata(self):
        self.job(country="USA")
        html = self.client.get(reverse("jobs:nonprofit_hiring_locations"))
        self.assertEqual(html.status_code, 200)
        self.assertContains(html, "not verified applicant eligibility")
        self.assertContains(html, "application/ld+json")
        self.assertContains(html, "https://remoteimpact.org/insights/nonprofit-hiring-locations/")
        response = self.client.get(reverse("jobs:nonprofit_hiring_locations_data"))
        data = response.json()
        self.assertEqual(data["observed_at"], html.context["report"]["observed_at"])
        self.assertEqual(data["total_listings"], 1)
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        self.assertEqual(self.client.post(reverse("jobs:nonprofit_hiring_locations_data")).status_code, 405)
        schema = json.loads(html.context["schema_json"])
        self.assertEqual(schema["distribution"]["contentUrl"], data["data_url"])
        self.assertNotIn("raw_data", response.content.decode())
        self.assertEqual(HiringEvidenceSitemap().location("nonprofit-hiring-locations"), reverse("jobs:nonprofit_hiring_locations"))

    def test_cache_reuse_and_empty_denominator(self):
        with self.assertNumQueries(2):
            data = get_snapshot()
        self.assertEqual(data["total_listings"], 0)
        self.assertTrue(all(row["percent"] is None for row in data["location_groups"]))
        with self.assertNumQueries(0):
            self.assertEqual(get_snapshot(), data)

    def test_user_content_is_escaped_and_not_in_schema(self):
        self.job(country='<img src=x onerror="alert(1)">')
        response = self.client.get(reverse("jobs:nonprofit_hiring_locations"))
        self.assertContains(response, "&lt;img")
        self.assertNotContains(response, '<img src=x')
        self.assertNotIn("onerror", response.context["schema_json"])
