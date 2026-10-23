from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from jobs.models import Job, Organization
from jobs.services.guide_generator import _create_job_hash


class ApplicationGuideSitemapTests(TestCase):
    def setUp(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider,
                provider_id=provider_id,
                name=provider,
                client_id="test-client",
                secret="test-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(Site.objects.get_current())

        self.organization = Organization.objects.create(name="Climate Lab", slug="climate-lab")
        self.personalized = self.create_job("personalized-guide")
        self.personalized.raw_data = {
            "application_guide": {
                "hash": _create_job_hash(self.personalized),
                "content": {"company_intro": "A current personalized guide."},
            }
        }
        self.personalized.save(update_fields=["raw_data"])

        self.fallback = self.create_job("fallback-guide")
        self.stale = self.create_job("stale-guide")
        self.stale.raw_data = {
            "application_guide": {"hash": "old-hash", "content": {"company_intro": "Old guide."}}
        }
        self.stale.save(update_fields=["raw_data"])

        self.unavailable = self.create_job("unavailable-guide")
        self.unavailable.is_active = False
        self.unavailable.raw_data = {
            "application_guide": {
                "hash": _create_job_hash(self.unavailable),
                "content": {"company_intro": "Guide for a closed role."},
            }
        }
        self.unavailable.save(update_fields=["is_active", "raw_data"])

    def create_job(self, slug):
        return Job.objects.create(
            title="Climate Researcher",
            slug=slug,
            organization=self.organization,
            description="Research climate solutions.",
            source=Job.Source.MANUAL,
        )

    def test_xml_lists_only_current_personalized_guides_for_available_jobs(self):
        cache.clear()
        sitemap_index = self.client.get("/sitemap.xml")
        self.assertEqual(sitemap_index.status_code, 200)
        self.assertContains(sitemap_index, "/sitemap-application-guides.xml")

        response = self.client.get(
            reverse("django.contrib.sitemaps.views.sitemap", kwargs={"section": "application-guides"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/jobs/personalized-guide/application-guide/")
        self.assertNotContains(response, "/jobs/fallback-guide/application-guide/")
        self.assertNotContains(response, "/jobs/stale-guide/application-guide/")
        self.assertNotContains(response, "/jobs/unavailable-guide/application-guide/")

    def test_listed_guide_has_indexable_self_canonical_html(self):
        response = self.client.get(reverse("jobs:application_guide", args=[self.personalized.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="robots" content="index, follow"')
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://remoteimpact.org/jobs/{self.personalized.slug}/application-guide/">',
            html=True,
        )
