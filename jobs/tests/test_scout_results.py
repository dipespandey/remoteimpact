from datetime import timedelta
from pathlib import Path
import os
from unittest.mock import patch
from urllib.parse import urlencode

from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import AssistantSubscription, ConsumerFunnelEvent as E, Job, JobAlert, JobAlertDelivery, Organization
from jobs.services.job_scout_service import JobScoutService


def setup_scout_fixture(case):
    site = Site.objects.get_current()
    for provider, pid in [("google", ""), ("openid_connect", "linkedin")]:
        app = SocialApp.objects.create(provider=provider, provider_id=pid, name=pid or provider, client_id="test-client", secret="test-secret")
        app.sites.add(site)
    org = Organization.objects.create(name="Climate Test Org", slug="climate-test-org")
    with patch("jobs.services.embedding_service.embed_job", return_value=None):
        case.job = Job.objects.create(title="Climate Python Engineer", slug="climate-python-result", organization=org, description="Build useful climate software.", requirements="Python", application_url="https://example.org/apply")
    case.intent = "Remote Python engineering roles in Europe at climate organizations."
    case.prefs = {"titles": ["engineer"], "skills": ["python"], "locations": ["Europe"], "category_slugs": []}


@override_settings(SECURE_SSL_REDIRECT=False, ACCOUNT_RATE_LIMITS={})
class ScoutResultTests(TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch("jobs.services.email_service.email_service.send_email", return_value=True).start()
        setup_scout_fixture(self)
        self.user = get_user_model().objects.create_user("result-user", "result@example.org", "test-password-531!")
        self.client.force_login(self.user)

    def create_scout(self, matches, sent=True, diagnostics=None):
        def matching(alert, **kwargs):
            kwargs["diagnostics"].update(diagnostics or {})
            return matches
        with patch.object(JobScoutService, "parse_intent", return_value=self.prefs), patch.object(JobScoutService, "get_matches", side_effect=matching) as search, patch("jobs.views.job_alerts.email_service.send_job_alert", return_value=sent) as mail:
            response = self.client.post(reverse("jobs:job_alert_create"), {"intent_text": self.intent, "frequency": "once", "source": "home_hero_example_python"})
        self.assertEqual(search.call_count, 1)
        self.assertEqual(mail.call_count, bool(matches))
        return response.url

    def result(self, url):
        with patch.object(JobScoutService, "get_matches", side_effect=AssertionError("GET reran matching")):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response

    def test_empty_redirect_and_refresh_preserve_incomplete_review_and_free_credit(self):
        url = self.create_scout([], diagnostics={"review_incomplete": True, "empty_reason": "review_incomplete", "hard_rejections": {"location": 20}})
        for _ in range(2):
            page = self.result(url)
            self.assertContains(page, "Match review is temporarily incomplete")
            self.assertNotContains(page, "This Scout returned no strong matches")
            self.assertNotContains(page, "Most current roles like this")
            self.assertNotContains(page, "Keep this exact search running")
            self.assertEqual(page.context["form"].initial["intent_text"], self.intent)
        self.assertEqual(E.objects.filter(event=E.Event.INITIAL_MATCHES_EMPTY).count(), 1)
        self.assertIsNone(AssistantSubscription.objects.get(user=self.user).free_job_scout_used_at)
        self.assertFalse(JobAlertDelivery.objects.exists())

    def test_email_failure_restores_rank_and_caveats_without_delivery(self):
        self.job.scout_score = 77
        self.job.scout_reasons = ["Python role", "Check: confirm hiring location"]
        self.job.scout_needs_confirmation = True
        self.job.scout_caveats = ["location_uncertain"]
        url = self.create_scout([self.job], sent=False)
        page = self.result(url)
        restored = page.context["preview_jobs"][0]
        self.assertEqual(restored.scout_score, 77)
        self.assertTrue(restored.scout_needs_confirmation)
        self.assertEqual(restored.scout_caveats, ["location_uncertain"])
        self.assertContains(page, "the email could not be sent")
        self.assertContains(page, self.job.title)
        self.assertNotContains(page, "Keep this exact search running")
        self.assertNotContains(page, "You received the one-time")
        self.assertFalse(JobAlertDelivery.objects.exists())
        self.assertTrue(E.objects.filter(event=E.Event.INITIAL_EMAIL_FAILED).exists())
        self.assertFalse(E.objects.filter(event=E.Event.INITIAL_EMAIL_SENT).exists())
        self.assertIsNone(AssistantSubscription.objects.get(user=self.user).free_job_scout_used_at)

    def test_success_snapshot_keeps_feedback_and_does_not_include_later_deliveries(self):
        url = self.create_scout([self.job])
        delivery = JobAlertDelivery.objects.get()
        delivery.feedback = "relevant"
        delivery.save(update_fields=["feedback"])
        with patch("jobs.services.embedding_service.embed_job", return_value=None):
            later = Job.objects.create(title="Later job", slug="later-job", organization=self.job.organization, description="Another role")
        JobAlertDelivery.objects.create(alert=delivery.alert, job=later)
        page = self.result(url)
        self.assertEqual([j.pk for j in page.context["preview_jobs"]], [self.job.pk])
        self.assertEqual(page.context["preview_jobs"][0].scout_feedback, "relevant")
        self.assertContains(page, "Mark as a useful match")
        self.assertNotContains(page, "Later job")

    def test_owner_can_retry_failed_saved_email_once_without_rematching(self):
        from django.core.cache import cache
        cache.clear()
        url = self.create_scout([self.job], sent=False)
        alert = JobAlert.objects.get(user=self.user)
        retry = reverse("jobs:job_alert_retry_email", kwargs={"pk": alert.pk})
        self.assertContains(self.result(url), "Retry email with these saved matches")
        with patch.object(JobScoutService, "get_matches", side_effect=AssertionError("Retry reran matching")), \
             patch("jobs.views.job_alerts.email_service.send_job_alert", return_value=True) as mail:
            self.assertEqual(self.client.get(retry).status_code, 405)
            self.assertEqual(self.client.post(retry).status_code, 302)
            self.assertEqual(self.client.post(retry).status_code, 302)
        mail.assert_called_once()
        self.assertEqual(JobAlertDelivery.objects.filter(alert=alert).count(), 1)
        self.assertIsNotNone(AssistantSubscription.objects.get(user=self.user).free_job_scout_used_at)
        self.assertNotContains(self.result(url), "Retry email with these saved matches")

    def test_retry_is_owner_only_and_refuses_expired_matches(self):
        from django.core.cache import cache
        cache.clear()
        self.create_scout([self.job], sent=False)
        alert = JobAlert.objects.get(user=self.user)
        retry = reverse("jobs:job_alert_retry_email", kwargs={"pk": alert.pk})
        other = get_user_model().objects.create_user("retry-other", "retry-other@example.org", "test-password-531!")
        self.client.force_login(other)
        with patch("jobs.views.job_alerts.email_service.send_job_alert") as mail:
            self.assertEqual(self.client.post(retry).status_code, 404)
        mail.assert_not_called()
        self.client.force_login(self.user)
        Job.objects.filter(pk=self.job.pk).update(is_active=False)
        with patch("jobs.views.job_alerts.email_service.send_job_alert") as mail:
            self.assertEqual(self.client.post(retry).status_code, 302)
        mail.assert_not_called()

    def test_failed_retry_keeps_credit_and_is_throttled(self):
        from django.core.cache import cache
        cache.clear()
        self.create_scout([self.job], sent=False)
        alert = JobAlert.objects.get(user=self.user)
        retry = reverse("jobs:job_alert_retry_email", kwargs={"pk": alert.pk})
        with patch("jobs.views.job_alerts.email_service.send_job_alert", return_value=False) as mail:
            self.client.post(retry)
            self.client.post(retry)
        mail.assert_called_once()
        self.assertIsNone(AssistantSubscription.objects.get(user=self.user).free_job_scout_used_at)
        self.assertFalse(JobAlertDelivery.objects.exists())

    def test_retry_cannot_deliver_a_second_free_scout_after_another_succeeded(self):
        from django.core.cache import cache
        cache.clear()
        self.create_scout([self.job], sent=False)
        failed = JobAlert.objects.get(user=self.user)
        other = JobAlert.objects.create(user=self.user, intent_text="Another search")
        E.objects.create(user=self.user, alert=other, event=E.Event.INITIAL_EMAIL_SENT)
        AssistantSubscription.objects.filter(user=self.user).update(free_job_scout_used_at=timezone.now())
        with patch("jobs.views.job_alerts.email_service.send_job_alert") as mail:
            self.client.post(reverse("jobs:job_alert_retry_email", kwargs={"pk": failed.pk}))
        mail.assert_not_called()

    def test_retry_requires_csrf_token(self):
        from django.test import Client
        self.create_scout([self.job], sent=False)
        alert = JobAlert.objects.get(user=self.user)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        with patch("jobs.views.job_alerts.email_service.send_job_alert") as mail:
            response = client.post(reverse("jobs:job_alert_retry_email", kwargs={"pk": alert.pk}))
        self.assertEqual(response.status_code, 403)
        mail.assert_not_called()

    def test_expired_snapshot_is_not_presented_as_empty_search(self):
        url = self.create_scout([self.job])
        Job.objects.filter(pk=self.job.pk).update(expires_at=timezone.now()-timedelta(days=1))
        page = self.result(url)
        self.assertContains(page, "The roles from this result are no longer available")
        self.assertNotContains(page, "This Scout returned no strong matches")
        self.assertEqual(page.context["preview_jobs"], [])

    def test_legacy_sent_result_uses_available_deliveries(self):
        alert = JobAlert.objects.create(user=self.user, intent_text=self.intent, frequency="once")
        JobAlertDelivery.objects.create(alert=alert, job=self.job, match_reasons=["Check: location"])
        page = self.result(reverse("jobs:job_alerts")+f"?created={alert.pk}")
        self.assertContains(page, self.job.title)
        self.assertContains(page, "Review the details")
        Job.objects.filter(pk=self.job.pk).update(is_active=False)
        self.assertContains(self.result(reverse("jobs:job_alerts")+f"?created={alert.pk}"), "The roles from this result are no longer available")

    def test_legacy_missing_result_never_runs_matching(self):
        alert = JobAlert.objects.create(user=self.user, intent_text=self.intent, frequency="once")
        page = self.result(reverse("jobs:job_alerts")+f"?created={alert.pk}")
        self.assertContains(page, "This saved result is unavailable")
        self.assertNotContains(page, "This Scout returned no strong matches")

    def test_other_user_cannot_read_result_snapshot(self):
        url = self.create_scout([self.job], sent=False)
        other = get_user_model().objects.create_user("other-user", "other@example.org", "test-password-531!")
        self.client.force_login(other)
        page = self.result(url)
        self.assertNotIn("created_alert", page.context)
        self.assertNotContains(page, self.intent)
        self.assertNotContains(page, self.job.title)

    def test_legacy_empty_outcome_retains_known_empty_state(self):
        alert = JobAlert.objects.create(user=self.user, intent_text=self.intent, frequency="once")
        E.objects.create(alert=alert, user=self.user, event=E.Event.INITIAL_MATCHES_EMPTY, metadata={"matching": {"hard_rejections": {"location": 4}}})
        page = self.result(reverse("jobs:job_alerts")+f"?created={alert.pk}")
        self.assertContains(page, "Most current roles like this")
        self.assertNotContains(page, 'add &quot;worldwide&quot;')

    def test_real_email_signup_preserves_brief_and_source(self):
        self.client.logout()
        next_path = reverse("jobs:job_alerts")+"?"+urlencode({"intent": self.intent, "source": "home_hero_example_python"})
        signup = self.client.get(reverse("jobs:job_scout_signup_start"), {"next": next_path})
        self.assertContains(self.client.get(signup.url), "Continue to my Scout")
        page = self.client.post(signup.url, {"email": "new-scout@example.org", "password1": "test-new-scout-password-531!", "password2": "test-new-scout-password-531!", "next": next_path}, follow=True)
        self.assertTrue(page.wsgi_request.user.is_authenticated)
        self.assertEqual(page.context["form"].initial["intent_text"], self.intent)
        self.assertContains(page, 'name="source" value="home_hero_example_python"')
        self.assertFalse(JobAlert.objects.exists())


@override_settings(SECURE_SSL_REDIRECT=False, ACCOUNT_RATE_LIMITS={})
class ScoutResultsBrowserTests(StaticLiveServerTestCase):
    def test_failed_email_retry_uses_saved_matches(self):
        from django.conf import settings
        from playwright.sync_api import sync_playwright
        from jobs.services.scout_results import snapshot_matches
        setup_scout_fixture(self)
        user = get_user_model().objects.create_user("browser-retry", "retry-browser@example.org", "test-browser-password-531!")
        self.client.force_login(user)
        alert = JobAlert.objects.create(user=user, intent_text=self.intent, frequency="once", is_active=False)
        E.objects.create(user=user, alert=alert, event=E.Event.INITIAL_EMAIL_FAILED,
                         metadata={"results_version": 1, "results": snapshot_matches([self.job]), "matching": {}})
        screenshots = Path(os.environ.get("SCOUT_SCREENSHOT_DIR", "/tmp/scout-results-browser"))
        screenshots.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, **({"executable_path": os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]} if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") else {}))
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                 "value": self.client.cookies[settings.SESSION_COOKIE_NAME].value,
                                 "url": self.live_server_url}])
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.live_server_url) else route.abort())
            page = context.new_page()
            with patch.object(JobScoutService, "get_matches", side_effect=AssertionError("Browser retry reran matching")), \
                 patch("jobs.views.job_alerts.email_service.send_job_alert", return_value=True) as mail:
                page.goto(self.live_server_url + reverse("jobs:job_alerts") + f"?created={alert.pk}")
                button = page.get_by_role("button", name="Retry email with these saved matches")
                button.wait_for()
                page.screenshot(path=str(screenshots / "email-retry-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                button.scroll_into_view_if_needed()
                page.screenshot(path=str(screenshots / "email-retry-mobile.png"))
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
                button.click()
                page.get_by_text("We emailed your saved matches.", exact=True).wait_for()
                page.reload()
                self.assertEqual(page.get_by_role("button", name="Retry email with these saved matches").count(), 0)
            mail.assert_called_once()
            browser.close()
        self.assertEqual(JobAlertDelivery.objects.filter(alert=alert).count(), 1)

    def test_signup_to_incomplete_result_and_refresh(self):
        from playwright.sync_api import sync_playwright
        setup_scout_fixture(self)
        screenshots = Path(os.environ.get("SCOUT_SCREENSHOT_DIR", "/tmp/scout-results-browser"))
        screenshots.mkdir(parents=True, exist_ok=True)
        def empty(alert, **kwargs):
            kwargs["diagnostics"].update({"review_incomplete": True, "empty_reason": "review_incomplete"})
            return []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, **({"executable_path": os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]} if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") else {}))
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.live_server_url) else route.abort())
            page = context.new_page()
            with patch.object(JobScoutService, "search_intent", return_value=({}, [self.job])):
                page.goto(self.live_server_url)
                page.get_by_role("link", name="Python in Europe").click()
                page.get_by_role("link", name="Use this exact search").click()
                page.get_by_role("heading", name="Create an account to finish your Scout").wait_for()
                page.locator(".card").first.screenshot(path=str(screenshots/"signup-step.png"))
                page.locator('#id_email').fill("browser-scout@example.org")
                page.locator('[name="password1"]').fill("test-browser-scout-password-531!")
                page.locator('[name="password2"]').fill("test-browser-scout-password-531!")
                page.get_by_role("button", name="Continue to my Scout").click()
                page.wait_for_url("**/alerts/?**")
            self.assertIn("home_hero_example_python", page.url)
            self.assertIn("Python", page.locator('[name="intent_text"]').input_value())
            with patch.object(JobScoutService, "parse_intent", return_value=self.prefs), patch.object(JobScoutService, "get_matches", side_effect=empty) as matching, patch("jobs.views.job_alerts.email_service.send_job_alert") as mail:
                page.get_by_role("button", name="Find and email my matches").click()
                page.get_by_text("Match review is temporarily incomplete.", exact=True).wait_for()
                page.reload()
                page.get_by_text("Match review is temporarily incomplete.", exact=True).wait_for()
                self.assertEqual(matching.call_count, 1)
                mail.assert_not_called()
                page.screenshot(path=str(screenshots/"incomplete-result.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(screenshots/"incomplete-result-mobile.png"))
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            browser.close()
