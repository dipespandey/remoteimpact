from html.parser import HTMLParser
import os
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from allauth.socialaccount.models import SocialApp
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse

from jobs.models import Category, ConsumerFunnelEvent, Job, Organization
from jobs.services.job_scout_service import JobScoutService


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current = {"attrs": dict(attrs), "text": []}

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.current["text"] = " ".join(" ".join(self.current["text"]).split())
            self.anchors.append(self.current)
            self.current = None


class ScoutActivationFlowTests(TestCase):
    def setUp(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider,
                provider_id=provider_id,
                name=provider_id or provider,
                client_id="test-client",
                secret="test-secret",
            )
            app.sites.add(site)
        org = Organization.objects.create(name="Climate Example", slug="climate-example")
        category = Category.objects.create(name="Climate", slug="climate-environment")
        self.job = Job.objects.create(
            title="Climate Data Engineer",
            slug="climate-data-engineer-example",
            organization=org,
            category=category,
            description="Build useful climate data tools.",
            requirements="Python experience.",
            application_url="https://example.org/apply",
        )

    def test_homepage_example_runs_through_preview_to_free_signup_handoff(self):
        home = self.client.get(reverse("jobs:home"))
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Preview free. Get one matching email when you sign up.")

        anchors = AnchorParser()
        anchors.feed(home.content.decode())
        python_example = next(
            anchor for anchor in anchors.anchors
            if anchor["text"] == "Python in Europe"
        )
        preview_url = python_example["attrs"]["href"]
        parsed_preview_url = urlsplit(preview_url)
        preview_query = parse_qs(parsed_preview_url.query)
        self.assertEqual(preview_query["source"], ["home_hero_example_python"])
        self.assertIn("Python", preview_query["intent"][0])

        with patch.object(
            JobScoutService,
            "search_intent",
            return_value=({}, [self.job]),
        ):
            preview = self.client.get(preview_url)
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, self.job.title)
        self.assertContains(preview, "No card. No recurring emails.")

        preview_anchors = AnchorParser()
        preview_anchors.feed(preview.content.decode())
        signup_link = next(
            anchor for anchor in preview_anchors.anchors
            if anchor["attrs"].get("data-track") == "scout_preview_signup"
        )
        signup_start = self.client.get(signup_link["attrs"]["href"])
        self.assertEqual(signup_start.status_code, 302)
        signup_target = urlsplit(signup_start["Location"])
        self.assertEqual(signup_target.path, reverse("account_signup"))
        next_path = parse_qs(signup_target.query)["next"][0]
        self.assertIn("home_hero_example_python", next_path)
        self.assertTrue(
            ConsumerFunnelEvent.objects.filter(
                event=ConsumerFunnelEvent.Event.SCOUT_SIGNUP_STARTED,
                metadata__source="home_hero_example_python",
            ).exists()
        )


class EmployerCheckoutRecoveryFlowTests(TestCase):
    def setUp(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider,
                provider_id=provider_id,
                name=provider_id or provider,
                client_id="test-client",
                secret="test-secret",
            )
            app.sites.add(site)
        self.poster = get_user_model().objects.create_user(
            "employer", "employer@example.org", "safe-test-password"
        )
        org = Organization.objects.create(name="Mission Employer", slug="mission-employer")
        self.draft = Job.objects.create(
            title="Remote Program Lead",
            slug="remote-program-lead-draft",
            organization=org,
            description="Lead a remote mission-driven program.",
            requirements="Relevant experience.",
            poster=self.poster,
            is_paid=False,
        )

    def test_cancel_returns_owner_to_saved_draft_and_reopens_checkout_form(self):
        self.client.force_login(self.poster)
        cancel_url = reverse("jobs:payment_cancel") + "?" + urlencode({"job_id": self.draft.pk})
        cancelled = self.client.get(cancel_url)
        expected = reverse("jobs:job_checkout", kwargs={"slug": self.draft.slug})
        self.assertRedirects(cancelled, expected, fetch_redirect_response=False)

        resume = self.client.get(expected)
        self.assertEqual(resume.status_code, 200)
        self.assertContains(resume, "Review and pay")
        self.assertContains(resume, self.draft.title)
        self.assertContains(resume, "Payment cancelled. Your job has been saved as a draft.")

    def test_cancel_does_not_expose_another_employers_draft(self):
        another_employer = get_user_model().objects.create_user(
            "another-employer", "another@example.org", "safe-test-password"
        )
        self.client.force_login(another_employer)
        response = self.client.get(
            reverse("jobs:payment_cancel"), {"job_id": self.draft.pk}
        )
        self.assertRedirects(response, reverse("jobs:home"), fetch_redirect_response=False)


class GrowthBrowserE2ETests(StaticLiveServerTestCase):
    """Exercise the acquisition and recovery surfaces in a real browser."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider,
                provider_id=provider_id,
                name=provider_id or provider,
                client_id="test-client",
                secret="test-secret",
            )
            app.sites.add(site)
        cls.poster = get_user_model().objects.create_user(
            "browser-employer", "browser-employer@example.org", "safe-test-password"
        )
        org = Organization.objects.create(name="Browser Climate Org", slug="browser-climate-org")
        category = Category.objects.create(name="Climate", slug="climate-environment")
        cls.job = Job.objects.create(
            title="Climate Data Engineer",
            slug="browser-climate-data-engineer",
            organization=org,
            category=category,
            description="Build useful climate data tools.",
            requirements="Python experience.",
            application_url="https://example.org/apply",
            poster=cls.poster,
            is_paid=False,
        )

    def test_scout_partner_feed_and_employer_cancel_journeys_in_browser(self):
        from playwright.sync_api import sync_playwright

        self.client.force_login(self.poster)
        session_cookie = self.client.cookies["sessionid"].value
        with sync_playwright() as playwright:
            browser_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
            if browser_path and not Path(browser_path).is_file():
                self.fail("PLAYWRIGHT_CHROMIUM_EXECUTABLE does not point to a browser binary")
            try:
                browser = playwright.chromium.launch(
                    headless=True,
                    **({"executable_path": browser_path} if browser_path else {}),
                )
            except Exception as exc:
                self.skipTest(f"Playwright Chromium is unavailable: {exc}")

            context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
            page = context.new_page()
            page.goto(self.live_server_url)
            with patch.object(JobScoutService, "search_intent", return_value=({}, [self.job])):
                page.get_by_role("link", name="Python in Europe").click()
                page.get_by_role("heading", name="Try this search").wait_for()
                page.get_by_role("heading", name=self.job.title).wait_for()
                page.get_by_role("link", name="Use this exact search").click()
            page.wait_for_url("**/accounts/signup/**")
            self.assertIn("home_hero_example_python", page.url)

            page.goto(self.live_server_url + reverse("jobs:partner_resources"))
            page.get_by_role("button", name="Copy link").click()
            page.get_by_role("status", name="").filter(has_text="Feed link copied.").wait_for()
            feed_response = page.request.get(self.live_server_url + reverse("jobs:jobs_feed"))
            self.assertEqual(feed_response.status, 200)
            self.assertIn("utm_campaign=latest_jobs_feed", feed_response.text())

            context.add_cookies([{
                "name": "sessionid",
                "value": session_cookie,
                "url": self.live_server_url,
            }])
            cancel_url = reverse("jobs:payment_cancel") + "?" + urlencode({"job_id": self.job.pk})
            page.goto(self.live_server_url + cancel_url)
            page.wait_for_url("**/jobs/browser-climate-data-engineer/checkout/")
            page.get_by_role("heading", name="Review and pay").wait_for()
            self.assertTrue(page.get_by_label("Job title").is_visible())
            browser.close()
