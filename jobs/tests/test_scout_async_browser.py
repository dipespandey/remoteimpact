import os
import threading
from pathlib import Path
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.utils import timezone

from jobs.models import ConsumerFunnelEvent, JobAlert, JobAlertDelivery, ScoutPreviewTask
from jobs.services.scout_tasks import _serialize_match, claim_task
from jobs.tests.test_scout_results import setup_scout_fixture


@override_settings(SECURE_SSL_REDIRECT=False, ACCOUNT_RATE_LIMITS={}, SCOUT_PREVIEW_ASYNC_ENABLED=True)
class ScoutAsyncPreviewBrowserTests(StaticLiveServerTestCase):
    def test_progress_completes_to_results_without_sending_email_and_loading_button_works(self):
        from playwright.sync_api import expect, sync_playwright

        setup_scout_fixture(self)
        brief = "Remote Python engineering roles in Europe at climate organizations."
        self.job.scout_score = 86
        self.job.scout_reasons = ["Python experience is listed"]
        self.job.scout_needs_confirmation = False
        result = {
            "preferences": self.prefs,
            "matches": [_serialize_match(self.job)],
            "older_jobs": [],
            "diagnostics": {"review_incomplete": False},
        }
        worker_started = threading.Event()
        allow_worker_finish = threading.Event()
        worker_result = []

        def call_in_database_thread(function, *args):
            result_box = []
            error_box = []
            def invoke():
                try:
                    result_box.append(function(*args))
                except Exception as exc:
                    error_box.append(exc)
            thread = threading.Thread(target=invoke, daemon=True)
            thread.start()
            thread.join(timeout=10)
            if thread.is_alive():
                raise AssertionError("database helper thread did not finish")
            if error_box:
                raise error_box[0]
            return result_box[0]

        def delayed_synthetic_compute(_task_id):
            worker_started.set()
            if not allow_worker_finish.wait(timeout=10):
                raise TimeoutError("browser test worker gate timed out")
            return result

        chrome = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        if not chrome and Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").exists():
            chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        screenshots = Path(".audit/scout-loading-20261023")
        screenshots.mkdir(parents=True, exist_ok=True)

        with patch("jobs.services.scout_tasks.compute_task", side_effect=delayed_synthetic_compute), \
             patch("jobs.views.job_alerts.email_service.send_job_alert") as send_alert, \
             patch("jobs.services.email_service.email_service.send_email") as send_email, \
             sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, **({"executable_path": chrome} if chrome else {}))
            page = browser.new_page(viewport={"width": 390, "height": 844})
            status_responses = []
            page.on("response", lambda response: status_responses.append(response.status)
                    if "/alerts/preview/status/" in response.url else None)
            page.route("**/*", lambda route: route.continue_()
                       if route.request.url.startswith(self.live_server_url) else route.abort())
            page.goto(self.live_server_url + "/alerts/preview/?intent=" + brief.replace(" ", "+"))
            expect(page.get_by_role("heading", name="Loading your matches")).to_be_visible()
            progress = page.get_by_role("status")
            expect(progress).to_be_visible()
            expect(progress.get_by_text("We’re checking current openings against your brief.")).to_be_visible()
            spinner = page.locator(".scout-preview-progress-spinner")
            expect(spinner).to_be_visible()
            expect(page.locator("#preview-poll-limit")).to_be_hidden()
            expect(page.locator("#preview-check-again")).to_be_hidden()
            self.assertTrue(spinner.evaluate("el => el.getAttribute('aria-hidden') === 'true'"))
            page.emulate_media(reduced_motion="reduce")
            self.assertEqual(spinner.evaluate("el => getComputedStyle(el).animationName"), "none")
            page.emulate_media(reduced_motion="no-preference")
            expect(page.get_by_text("Matching your brief and checking listing evidence.", exact=False)).to_be_visible()
            page.screenshot(path=str(screenshots / "preview-loading-mobile.png"), full_page=True)
            status_url = page.evaluate("""() => {
                const script = [...document.scripts].find(item => item.textContent.includes('Preview status unavailable'));
                const raw = script.textContent.match(/const statusUrl = (\".*?\");/s)[1];
                return JSON.parse(raw);
            }""")
            other_page = browser.new_page()
            other_response = other_page.goto(self.live_server_url + status_url)
            self.assertEqual(other_response.status, 404)
            self.assertEqual(other_page.locator("body").inner_text(), '{"status": "expired"}')
            other_page.close()

            claimed = call_in_database_thread(claim_task)
            self.assertIsNotNone(claimed)
            task_id, lease_token = claimed

            def run_worker():
                from jobs.services.scout_tasks import execute_claimed_task
                worker_result.append(execute_claimed_task(task_id, lease_token))

            worker = threading.Thread(target=run_worker, daemon=True)
            worker.start()
            self.assertTrue(worker_started.wait(timeout=5), "worker did not start synthetic computation")
            page.wait_for_timeout(1800)
            self.assertIn(202, status_responses, "browser should observe in-progress polling before completion")
            self.assertFalse(send_alert.called)
            self.assertFalse(send_email.called)
            allow_worker_finish.set()

            expect(page.get_by_role("heading", name=self.job.title)).to_be_visible(timeout=10000)
            expect(page.get_by_role("heading", name="Your job matches")).to_be_visible()
            page.screenshot(path=str(screenshots / "preview-results-mobile.png"), full_page=True)
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), "worker did not finish after the test released it")
            self.assertEqual(worker_result, [True])
            self.assertIn(200, status_responses, "browser should observe the completed polling response")
            self.assertFalse(send_alert.called)
            self.assertFalse(send_email.called)

            def mark_task_failed(task_id):
                task = ScoutPreviewTask.objects.get(pk=task_id)
                task.state = ScoutPreviewTask.State.FAILED
                task.last_error = "TimeoutError"
                task.completed_at = timezone.now()
                task.save(update_fields=["state", "last_error", "completed_at"])

            call_in_database_thread(mark_task_failed, task_id)
            page.reload()
            expect(page.get_by_role("heading", name="Match review is temporarily incomplete")).to_be_visible()
            retry_button = page.get_by_role("button", name="Retry this search").first
            expect(retry_button).to_be_visible()
            page.screenshot(path=str(screenshots / "preview-retry-mobile.png"), full_page=True)

            worker_started.clear()
            allow_worker_finish.clear()
            retry_button.click()
            expect(page.get_by_role("heading", name="Loading your matches")).to_be_visible()
            expect(page.locator(".scout-preview-progress-spinner")).to_be_visible()
            claimed = call_in_database_thread(claim_task)
            self.assertIsNotNone(claimed)
            task_id, lease_token = claimed
            worker = threading.Thread(target=run_worker, daemon=True)
            worker.start()
            self.assertTrue(worker_started.wait(timeout=5), "retry worker did not start synthetic computation")
            page.wait_for_timeout(1800)
            self.assertIn(202, status_responses, "retry should remain visibly in progress while work is delayed")
            allow_worker_finish.set()
            expect(page.get_by_role("heading", name=self.job.title)).to_be_visible(timeout=10000)
            expect(page.get_by_role("heading", name="Your job matches")).to_be_visible()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), "retry worker did not finish after release")
            self.assertEqual(worker_result, [True, True])

            # Exercise the real loading listener, then stop only the navigation
            # so its disabled state and accessible progress label can be checked.
            page.get_by_text("Edit search", exact=True).click()
            form = page.locator("#personalize-brief")
            form.evaluate("form => form.addEventListener('submit', event => event.preventDefault(), {once: true})")
            button = page.locator("#personalize-brief button[type='submit']")
            button.click()
            expect(button).to_be_disabled()
            expect(button).to_have_attribute("aria-busy", "true")
            expect(button).to_contain_text("Finding matches")
            self.assertFalse(send_alert.called)
            self.assertFalse(send_email.called)
            browser.close()

        self.assertEqual(JobAlert.objects.count(), 0)
        self.assertEqual(JobAlertDelivery.objects.count(), 0)
        self.assertEqual(
            ConsumerFunnelEvent.objects.filter(event=ConsumerFunnelEvent.Event.SCOUT_PREVIEWED).count(), 1
        )
