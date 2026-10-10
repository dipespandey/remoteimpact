from datetime import timedelta
from unittest.mock import Mock, patch

from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job, JobDailyStat, JobFeaturePurchase, JobSlugRedirect, Organization
from jobs.services.job_service import JobService
from jobs.services.payment_service import PaymentService
from jobs.views.employers import report_token

User = get_user_model()
BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15"
DESCRIPTION = "A substantive description of a real remote role with responsibilities and outcomes. " * 4


class EmployerTestBase(TestCase):
    def setUp(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider, provider_id=provider_id, name=(provider_id or provider).title(),
                client_id="x", secret="x",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(site)
        self.org = Organization.objects.create(name="Climate Org")
        self.job = Job.objects.create(
            title="Program Manager", slug="program-manager-climate-org", organization=self.org,
            description=DESCRIPTION, application_url="https://jobs.example.org/apply/1", quality_score=80,
        )


class TrackingTests(EmployerTestBase):
    def test_view_beacon_counts_once_per_visitor_and_ignores_bots(self):
        url = reverse("jobs:job_view_beacon", kwargs={"slug": self.job.slug})
        self.client.post(url, HTTP_USER_AGENT=BROWSER)
        self.client.post(url, HTTP_USER_AGENT=BROWSER)
        self.client.post(url, HTTP_USER_AGENT="Googlebot/2.1")
        self.assertEqual(JobDailyStat.objects.get(job=self.job).views, 1)

    def test_apply_redirect_without_a_page_view_is_not_counted(self):
        from django.core.cache import cache
        cache.clear()
        url = reverse("jobs:job_apply_redirect", kwargs={"slug": self.job.slug})
        response = self.client.get(url, HTTP_USER_AGENT=BROWSER)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(JobDailyStat.objects.filter(job=self.job, apply_clicks__gt=0).exists())

    def test_apply_redirect_counts_and_sends_to_employer(self):
        from django.core.cache import cache
        cache.clear()
        self.client.post(reverse("jobs:job_view_beacon", kwargs={"slug": self.job.slug}), HTTP_USER_AGENT=BROWSER)
        url = reverse("jobs:job_apply_redirect", kwargs={"slug": self.job.slug})
        response = self.client.get(url, HTTP_USER_AGENT=BROWSER)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://jobs.example.org/apply/1?"))
        self.assertIn("utm_source=remoteimpact", response["Location"])
        self.assertIn("noindex", response["X-Robots-Tag"])
        self.assertEqual(JobDailyStat.objects.get(job=self.job).apply_clicks, 1)

    def test_apply_redirect_refuses_unsafe_destination(self):
        Job.objects.filter(pk=self.job.pk).update(application_url="javascript:alert(1)")
        response = self.client.get(reverse("jobs:job_apply_redirect", kwargs={"slug": self.job.slug}), HTTP_USER_AGENT=BROWSER)
        self.assertEqual(response.status_code, 404)

    def test_detail_page_routes_apply_through_counter(self):
        response = self.client.get(self.job.get_absolute_url())
        self.assertContains(response, reverse("jobs:job_apply_redirect", kwargs={"slug": self.job.slug}))
        self.assertContains(response, reverse("jobs:job_view_beacon", kwargs={"slug": self.job.slug}))

    def test_old_slug_redirects_permanently(self):
        JobSlugRedirect.objects.create(old_slug="program-manager-unknown-organization", job=self.job)
        response = self.client.get("/jobs/program-manager-unknown-organization/")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], self.job.get_absolute_url())


class FeaturedPlacementTests(EmployerTestBase):
    def _event(self, session_id="cs_feature"):
        return {"id": f"evt_{session_id}", "type": "checkout.session.completed", "data": {"object": {
            "id": session_id, "mode": "payment", "payment_status": "paid", "amount_total": 4900,
            "customer_details": {"email": "hr@climate.example"},
            "metadata": {"product": "feature", "job_id": str(self.job.pk)},
        }}}

    def test_feature_payment_extends_window_once_per_session(self):
        PaymentService.handle_webhook_event(self._event())
        PaymentService.handle_webhook_event(self._event())
        self.job.refresh_from_db()
        self.assertTrue(self.job.is_sponsored)
        self.assertEqual(JobFeaturePurchase.objects.count(), 1)
        self.assertEqual(JobFeaturePurchase.objects.get().purchaser_email, "hr@climate.example")
        self.assertFalse(self.job.is_paid)  # an imported job isn't turned into a paid posting

    def test_second_purchase_stacks(self):
        PaymentService.handle_webhook_event(self._event("cs_a"))
        PaymentService.handle_webhook_event(self._event("cs_b"))
        self.job.refresh_from_db()
        self.assertGreater(self.job.featured_until - timezone.now(), timedelta(days=59))

    def test_featured_job_is_pinned_and_labelled_on_first_page_only(self):
        Job.objects.filter(pk=self.job.pk).update(featured_until=timezone.now() + timedelta(days=5))
        for i in range(25):
            Job.objects.create(title=f"Other {i}", slug=f"other-{i}", organization=self.org, description=DESCRIPTION, quality_score=80)
        first = self.client.get(reverse("jobs:job_list"))
        self.assertEqual([j.pk for j in first.context["sponsored_jobs"]], [self.job.pk])
        self.assertContains(first, "★ Featured")
        second = self.client.get(reverse("jobs:job_list"), {"page": 2})
        self.assertEqual(second.context["sponsored_jobs"], [])

    def test_expired_feature_is_not_pinned(self):
        Job.objects.filter(pk=self.job.pk).update(featured_until=timezone.now() - timedelta(minutes=1))
        response = self.client.get(reverse("jobs:job_list"))
        self.assertEqual(response.context["sponsored_jobs"], [])

    @override_settings(STRIPE_SECRET_KEY="sk_test_example")
    @patch("jobs.services.payment_service.stripe.checkout.Session.create")
    def test_feature_page_starts_checkout(self, create):
        create.return_value = Mock(url="https://checkout.example/feature")
        page = self.client.get(reverse("jobs:feature_job", kwargs={"slug": self.job.slug}))
        self.assertContains(page, "Feature for $49")
        response = self.client.post(reverse("jobs:feature_job", kwargs={"slug": self.job.slug}), {"source": "report"})
        self.assertRedirects(response, "https://checkout.example/feature", fetch_redirect_response=False)
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["metadata"]["product"], "feature")
        self.assertEqual(kwargs["line_items"][0]["price_data"]["unit_amount"], 4900)


class EmployerReportTests(EmployerTestBase):
    def test_report_shows_stats_for_signed_link_only(self):
        JobDailyStat.objects.create(job=self.job, date=timezone.localdate(), views=12, apply_clicks=3)
        response = self.client.get(reverse("jobs:employer_report", kwargs={"token": report_token(self.org)}))
        self.assertContains(response, "Climate Org on Remote Impact")
        self.assertContains(response, "Program Manager")
        self.assertEqual(response.context["total_views"], 12)
        self.assertEqual(response.context["total_clicks"], 3)
        bad = self.client.get(reverse("jobs:employer_report", kwargs={"token": "forged:token"}))
        self.assertEqual(bad.status_code, 404)

    def test_landing_page_renders_prices(self):
        response = self.client.get(reverse("jobs:employers"))
        self.assertContains(response, "$99")
        self.assertContains(response, "$49")
        self.assertEqual(self.client.get("/employers/pricing/").status_code, 301)


class PosterOrganizationTests(TestCase):
    def test_poster_cannot_take_over_an_owned_organization(self):
        owner = User.objects.create_user("owner", "owner@example.com", "pw-123456789")
        stranger = User.objects.create_user("stranger", "s@example.com", "pw-123456789")
        org = Organization.objects.create(name="Owned Org", website="https://owned.example", description="Real profile")
        org.members.add(owner)
        job = JobService.create_job({"title": "Role", "organization_name": "Owned Org", "organization_website": "https://evil.example",
                                     "organization_description": "Hijacked", "description": DESCRIPTION, "requirements": "Experience.", "location": "Remote"}, user=stranger)
        self.assertNotEqual(job.organization_id, org.pk)
        org.refresh_from_db()
        self.assertEqual(org.description, "Real profile")
        self.assertEqual(job.job_type, "full-time")

    def test_update_only_fills_missing_organization_fields(self):
        org = Organization.objects.create(name="Imported Org", website="https://imported.example", description="")
        job = Job.objects.create(title="Role", slug="role-imported-org", organization=org, description=DESCRIPTION)
        JobService.update_job(job, {"organization_name": "Imported Org", "organization_website": "https://evil.example",
                                    "organization_description": "Now described"})
        org.refresh_from_db()
        self.assertEqual(org.website, "https://imported.example")
        self.assertEqual(org.description, "Now described")
