from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from jobs.models import Category, Job, Organization


class PaidPostApplyTests(TestCase):
    def setUp(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        site = Site.objects.get_current()
        for provider in ("google", "openid_connect"):
            app = SocialApp.objects.create(provider=provider, name=provider, client_id="x", secret="y",
                                           provider_id="linkedin" if provider == "openid_connect" else "")
            app.sites.add(site)
        self.poster = get_user_model().objects.create_user("poster", "jobs@org.example", "pw")
        org = Organization.objects.create(name="Example Org", slug="example-org")
        cat = Category.objects.create(name="Impact", slug="impact")
        now = timezone.now()
        self.job = Job.objects.create(
            title="Program Lead", slug="program-lead", organization=org, category=cat, description="Lead programs.",
            location="Remote", job_type="full-time", source=Job.Source.MANUAL, is_active=True, is_paid=True,
            paid_at=now, posted_at=now, expires_at=now + timezone.timedelta(days=30),
            featured_until=now + timezone.timedelta(days=30), poster=self.poster,
            application_url="https://example.bamboohr.com/careers/1",
        )

    def test_paid_post_applies_on_remote_impact_with_own_site_secondary(self):
        response = self.client.get(self.job.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["apply_inplatform"])
        self.assertContains(response, "/apply/")
        self.assertContains(response, "apply on Example Org")

    def test_unpaid_feed_job_still_links_out(self):
        Job.objects.filter(pk=self.job.pk).update(source=Job.Source.MANUAL, is_paid=False)
        response = self.client.get(self.job.get_absolute_url())
        self.assertFalse(response.context["apply_inplatform"])
