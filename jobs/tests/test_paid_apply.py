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


class FeaturedNotDuplicatedTests(PaidPostApplyTests):
    def test_featured_role_listed_once_on_jobs_page(self):
        response = self.client.get("/jobs/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([j.id for j in response.context["sponsored_jobs"]], [self.job.id])
        self.assertNotIn(self.job.id, [j.id for j in response.context["jobs"]])
        self.assertNotContains(response, "No roles match these filters")


class EditPaidRoleTests(PaidPostApplyTests):
    def test_colleague_can_edit_paid_role_and_seekers_see_update(self):
        colleague = get_user_model().objects.create_user("sarah", "sarah@org.example", "pw")
        self.job.organization.members.add(colleague)
        Job.objects.filter(pk=self.job.pk).update(raw_data={"benefits": "Health", "source_note": "keep me"},
                                                  must_haves=[{"label": "x", "quote": "y"}])
        self.client.force_login(colleague)
        page = self.client.get(f"/jobs/{self.job.slug}/edit/")
        self.assertEqual(page.status_code, 200)
        data = {k: v for k, v in page.context["form"].initial.items() if v is not None}
        data.update({"title": "Program Lead (updated)", "description": "New description of the role. " * 10,
                     "requirements": "Five years running programs.",
                     "category": self.job.category_id, "custom_questions": "[]"})
        response = self.client.post(f"/jobs/{self.job.slug}/edit/", data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", {}) and response.context["form"].errors)
        self.job.refresh_from_db()
        self.assertEqual(self.job.title, "Program Lead (updated)")
        self.assertIsNotNone(self.job.edited_at)
        self.assertEqual(self.job.raw_data.get("source_note"), "keep me")
        self.assertEqual(self.job.must_haves, [])
        self.client.logout()
        self.assertContains(self.client.get(self.job.get_absolute_url()), "Last updated")

    def test_outsider_cannot_edit(self):
        outsider = get_user_model().objects.create_user("x", "x@else.example", "pw")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(f"/jobs/{self.job.slug}/edit/").status_code, 404)
