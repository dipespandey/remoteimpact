from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job, JobInterest, JobAlert, JobAlertDelivery, Organization, SavedJob, SharedShortlist
from jobs.services.job_scout_service import JobScoutService


@override_settings(DEEPSEEK_API_KEY="", RESEND_API_KEY="")
class ShortlistTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.candidate = User.objects.create_user(username="candidate", email="private@example.com")
        self.employer = User.objects.create_user(username="employer", email="employer@example.com")
        self.other = User.objects.create_user(username="other")
        self.org = Organization.objects.create(name="Test Impact", slug="test-impact")
        self.job = Job.objects.create(title="Python Engineer", slug="python-engineer", organization=self.org,
            description="Build Python software", skills=["python"], location="Remote", poster=self.employer,
            is_paid=True, is_active=True, posted_at=timezone.now())
        for provider, pid in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=pid, name=provider,
                client_id="test", secret="test", settings={"server_url": "https://www.linkedin.com/oauth"} if pid else {})
            app.sites.add(Site.objects.get_current())
        self.url = reverse("jobs:job_interest", args=[self.job.slug])
        self.employer_url = reverse("jobs:employer_shortlist", args=[self.job.slug])
        self.data = {"status": "saved", "share_with_employer": "on", "full_name": "Private Candidate",
                     "headline": "Python engineer", "evidence": "Built Python pipelines", "eligibility": "EU work permission"}
        self.client.force_login(self.candidate)

    def save_interest(self):
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        return JobInterest.objects.get(user=self.candidate, job=self.job)

    def test_private_by_default_and_idempotent(self):
        for _ in range(2):
            self.assertEqual(self.client.post(self.url, {"status": "saved"}).status_code, 302)
        interest = JobInterest.objects.get(user=self.candidate, job=self.job)
        self.assertFalse(interest.share_with_employer)
        self.client.force_login(self.employer)
        self.assertNotContains(self.client.get(self.employer_url), self.candidate.email)

    def test_explicit_consent_required_and_withdrawal_hides_immediately(self):
        self.client.post(self.url, {"status": "saved", "share_with_employer": "on"})
        self.assertFalse(JobInterest.objects.exists())
        item = self.save_interest()
        self.assertIsNotNone(item.consented_at)
        self.client.force_login(self.employer)
        self.assertContains(self.client.get(self.employer_url), "Private Candidate")
        self.client.force_login(self.candidate)
        self.client.post(self.url, {"status": "saved"})
        self.client.force_login(self.employer)
        self.assertNotContains(self.client.get(self.employer_url), "Private Candidate")

    def test_unrelated_employer_and_unpaid_poster_cannot_access_candidates(self):
        self.save_interest()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.employer_url).status_code, 404)
        self.job.is_paid = False
        self.job.save(update_fields=["is_paid"])
        self.client.force_login(self.employer)
        response = self.client.get(self.employer_url)
        self.assertContains(response, "Publish for $99")
        self.assertNotContains(response, "Private Candidate")

    def test_stale_consent_excluded(self):
        item = self.save_interest()
        JobInterest.objects.filter(pk=item.pk).update(consented_at=timezone.now() - timedelta(days=91))
        self.client.force_login(self.employer)
        self.assertNotContains(self.client.get(self.employer_url), "Private Candidate")

    def test_stage_is_scoped_and_candidate_can_see_it(self):
        item = self.save_interest()
        url = reverse("jobs:employer_interest_stage", args=[self.job.slug, item.pk])
        self.assertEqual(self.client.post(url, {"stage": "reviewing"}).status_code, 404)
        self.client.force_login(self.employer)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"stage": "unknown"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"stage": "reviewing"}).status_code, 302)
        self.client.force_login(self.candidate)
        self.assertContains(self.client.get(reverse("jobs:shortlist")), "Reviewing")

    def test_aggregated_role_never_shares_details(self):
        self.job.poster = None
        self.job.save(update_fields=["poster"])
        item = self.save_interest()
        self.assertFalse(item.share_with_employer)
        self.assertEqual(item.full_name, "")

    def test_closed_role_can_withdraw_but_cannot_add_interest(self):
        self.save_interest()
        self.job.is_active = False
        self.job.save(update_fields=["is_active"])
        self.client.post(self.url, {"status": "archived"})
        self.assertFalse(JobInterest.objects.get().share_with_employer)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.url, {"status": "saved"}).status_code, 404)

    def test_share_has_no_candidate_information_and_revokes(self):
        self.save_interest()
        self.assertEqual(self.client.post(reverse("jobs:share_shortlist"), {"jobs": [self.job.pk]}).status_code, 302)
        collection = SharedShortlist.objects.get()
        url = reverse("jobs:shared_shortlist", args=[collection.token])
        self.client.logout()
        response = self.client.get(url)
        self.assertContains(response, self.job.title)
        self.assertNotContains(response, "Private Candidate")
        self.assertNotContains(response, self.candidate.email)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertTrue(self.client.session["referral_code"])
        revoke = reverse("jobs:revoke_shortlist", args=[collection.token])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(revoke).status_code, 404)
        self.client.force_login(self.candidate)
        self.assertEqual(self.client.post(revoke).status_code, 302)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_cannot_share_arbitrary_or_closed_jobs(self):
        url = reverse("jobs:share_shortlist")
        self.assertEqual(self.client.post(url, {"jobs": [self.job.pk]}).status_code, 400)
        SavedJob.objects.create(user=self.candidate, job=self.job)
        self.job.is_active = False
        self.job.save(update_fields=["is_active"])
        self.assertEqual(self.client.post(url, {"jobs": [self.job.pk]}).status_code, 400)
        self.assertFalse(SharedShortlist.objects.exists())

    def test_shared_role_removed_when_closed(self):
        self.save_interest()
        self.client.post(reverse("jobs:share_shortlist"), {"jobs": [self.job.pk]})
        self.job.is_active = False
        self.job.save(update_fields=["is_active"])
        response = self.client.get(reverse("jobs:shared_shortlist", args=[SharedShortlist.objects.get().token]))
        self.assertNotContains(response, self.job.title)

    def test_tracker_collects_and_deduplicates_saved_delivered_and_interested(self):
        self.save_interest()
        SavedJob.objects.create(user=self.candidate, job=self.job)
        alert = JobAlert.objects.create(user=self.candidate)
        JobAlertDelivery.objects.create(alert=alert, job=self.job)
        response = self.client.get(reverse("jobs:shortlist"))
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_rejections_suppress_matches_across_scouts(self):
        alert = JobAlert.objects.create(user=self.candidate, parsed_preferences={"skills": ["python"]})
        JobInterest.objects.create(user=self.candidate, job=self.job, status="not_relevant")
        self.assertEqual(JobScoutService.get_matches(alert), [])
        JobInterest.objects.all().delete()
        other_alert = JobAlert.objects.create(user=self.candidate)
        JobAlertDelivery.objects.create(alert=other_alert, job=self.job, feedback="not_relevant")
        self.assertEqual(JobScoutService.get_matches(alert), [])

    @patch("jobs.management.commands.send_interest_notifications.email_service.send_email", return_value=True)
    def test_notifications_are_bounded_and_contain_no_candidate_details(self, send):
        self.save_interest()
        call_command("send_interest_notifications", stdout=StringIO())
        call_command("send_interest_notifications", stdout=StringIO())
        self.assertEqual(send.call_count, 1)
        self.assertNotIn(self.candidate.email, send.call_args.kwargs["html"])
        self.assertNotIn("Private Candidate", send.call_args.kwargs["html"])

    @patch("jobs.management.commands.send_interest_notifications.email_service.send_email", return_value=False)
    def test_notification_failure_keeps_pending_and_dry_run_never_sends(self, send):
        item = self.save_interest()
        call_command("send_interest_notifications", dry_run=True, stdout=StringIO())
        send.assert_not_called()
        call_command("send_interest_notifications", stdout=StringIO())
        item.refresh_from_db()
        self.assertIsNone(item.employer_notified_at)

    def test_user_deletion_removes_interests_and_shared_links(self):
        self.save_interest()
        self.client.post(reverse("jobs:share_shortlist"), {"jobs": [self.job.pk]})
        self.candidate.delete()
        self.assertFalse(JobInterest.objects.exists())
        self.assertFalse(SharedShortlist.objects.exists())

    def test_auth_csrf_and_safe_urls(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.candidate)
        self.assertEqual(csrf_client.post(self.url, self.data).status_code, 403)
        self.data["portfolio_url"] = "javascript:alert(1)"
        self.client.post(self.url, self.data)
        self.assertFalse(JobInterest.objects.exists())
        self.client.logout()
        self.assertEqual(self.client.get(reverse("jobs:shortlist")).status_code, 302)

    def test_public_tech_collection_uses_recent_jobs_only(self):
        Job.objects.create(title="Old Python role", slug="old", organization=self.org,
                           posted_at=timezone.now()-timedelta(days=50))
        response = self.client.get(reverse("jobs:tech_collection"))
        self.assertContains(response, self.job.title)
        self.assertNotContains(response, "Old Python role")
        self.assertContains(response, "index, follow")
