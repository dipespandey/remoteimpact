from datetime import timedelta
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from jobs.models import ConsumerFunnelEvent, ScoutPreviewAccess, ScoutPreviewQueueGuard, ScoutPreviewTask
from jobs.management.commands.run_scout_preview_worker import release_is_active
from jobs.services.scout_tasks import PreviewQueueLimited, claim_task, enqueue_preview, execute_claimed_task, prune_tasks, semantic_key, session_digest
from jobs.services.scout_tasks import hydrate_result, job_fingerprint
from jobs.tests.test_scout_results import setup_scout_fixture


INTENT = "Remote climate policy analyst roles in Europe with research experience."


class ScoutPreviewQueueTests(TestCase):
    def test_exact_brief_deduplicates_work_but_not_session_access(self):
        first, created = enqueue_preview(INTENT, "session-one")
        second, second_created = enqueue_preview(INTENT, "session-two")

        self.assertTrue(created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.semantic_key, semantic_key(INTENT))
        self.assertEqual(ScoutPreviewTask.objects.count(), 1)
        self.assertEqual(ScoutPreviewAccess.objects.filter(task=first).count(), 2)
        self.assertEqual(
            ScoutPreviewAccess.objects.get(
                task=first, session_digest=session_digest("session-two")
            ).enqueue_count,
            0,
        )

    def test_case_and_punctuation_remain_part_of_semantic_identity(self):
        original, _ = enqueue_preview(INTENT, "session-one")
        changed, _ = enqueue_preview(INTENT.replace("Europe", "europe"), "session-one")

        self.assertNotEqual(original.pk, changed.pk)

    def test_claim_leases_task_and_reclaims_expired_worker_lease(self):
        task, _ = enqueue_preview(INTENT, "session-one")
        task_id, token = claim_task()
        self.assertEqual(task_id, task.pk)
        task.refresh_from_db()
        self.assertEqual(task.state, ScoutPreviewTask.State.RUNNING)
        self.assertEqual(task.attempts, 1)

        task.lease_until = timezone.now() - timedelta(seconds=1)
        task.save(update_fields=["lease_until"])
        task_id, retry_token = claim_task()
        self.assertEqual(task_id, task.pk)
        self.assertNotEqual(token, retry_token)
        task.refresh_from_db()
        self.assertEqual(task.attempts, 2)
        self.assertEqual(task.state, ScoutPreviewTask.State.RUNNING)

    def test_old_worker_cannot_commit_after_lease_is_reclaimed(self):
        task, _ = enqueue_preview(INTENT, "session-one")
        _, old_token = claim_task()
        task.lease_until = timezone.now() - timedelta(seconds=1)
        task.save(update_fields=["lease_until"])
        _, new_token = claim_task()

        with patch("jobs.services.scout_tasks._compute_with_deadline", return_value={"matches": []}):
            self.assertFalse(execute_claimed_task(task.pk, old_token))

        task.refresh_from_db()
        self.assertEqual(task.state, ScoutPreviewTask.State.RUNNING)
        self.assertEqual(task.lease_token, new_token)

    @override_settings(SCOUT_PREVIEW_RESULT_TTL_SECONDS=10)
    def test_expired_result_is_requeued_within_session_limits(self):
        task, _ = enqueue_preview(INTENT, "session-one")
        task.state = ScoutPreviewTask.State.SUCCEEDED
        task.result = {"matches": []}
        task.completed_at = timezone.now() - timedelta(seconds=11)
        task.save(update_fields=["state", "result", "completed_at"])

        refreshed, created = enqueue_preview(INTENT, "session-one")

        self.assertFalse(created)
        self.assertEqual(refreshed.pk, task.pk)
        self.assertEqual(refreshed.state, ScoutPreviewTask.State.QUEUED)
        access = ScoutPreviewAccess.objects.get(task=task)
        self.assertEqual(access.enqueue_count, 2)

    def test_failed_task_requires_explicit_retry_and_retry_uses_session_limit(self):
        task, _ = enqueue_preview(INTENT, "session-one")
        task.state = ScoutPreviewTask.State.FAILED
        task.last_error = "TimeoutError"
        task.completed_at = timezone.now()
        task.save(update_fields=["state", "last_error", "completed_at"])

        unchanged, created = enqueue_preview(INTENT, "session-one")

        self.assertFalse(created)
        self.assertEqual(unchanged.state, ScoutPreviewTask.State.FAILED)
        self.assertEqual(ScoutPreviewAccess.objects.get(task=task).enqueue_count, 1)

        retried, created = enqueue_preview(INTENT, "session-one", retry_failed=True)

        self.assertFalse(created)
        self.assertEqual(retried.state, ScoutPreviewTask.State.QUEUED)
        self.assertEqual(ScoutPreviewAccess.objects.get(task=task).enqueue_count, 2)

    @override_settings(SCOUT_PREVIEW_SESSION_LIMIT=1)
    def test_explicit_failed_retry_obeys_session_rate_limit(self):
        task, _ = enqueue_preview(INTENT, "session-one")
        task.state = ScoutPreviewTask.State.FAILED
        task.completed_at = timezone.now()
        task.save(update_fields=["state", "completed_at"])

        with self.assertRaises(PreviewQueueLimited) as error:
            enqueue_preview(INTENT, "session-one", retry_failed=True)

        self.assertEqual(error.exception.reason, "session_limit")
        task.refresh_from_db()
        self.assertEqual(task.state, ScoutPreviewTask.State.FAILED)

    @override_settings(SCOUT_PREVIEW_SESSION_LIMIT=5)
    def test_new_work_and_retries_share_one_session_enqueue_budget(self):
        first, _ = enqueue_preview("First distinct session task", "bounded-session")
        second, _ = enqueue_preview("Second distinct session task", "bounded-session")
        for task in (first, second):
            task.state = ScoutPreviewTask.State.FAILED
            task.completed_at = timezone.now()
            task.save(update_fields=["state", "completed_at"])

        enqueue_preview("First distinct session task", "bounded-session", retry_failed=True)
        enqueue_preview("Second distinct session task", "bounded-session", retry_failed=True)
        first.refresh_from_db()
        first.state = ScoutPreviewTask.State.FAILED
        first.completed_at = timezone.now()
        first.save(update_fields=["state", "completed_at"])
        enqueue_preview("First distinct session task", "bounded-session", retry_failed=True)

        second.refresh_from_db()
        second.state = ScoutPreviewTask.State.FAILED
        second.completed_at = timezone.now()
        second.save(update_fields=["state", "completed_at"])
        with self.assertRaises(PreviewQueueLimited) as error:
            enqueue_preview("Second distinct session task", "bounded-session", retry_failed=True)

        self.assertEqual(error.exception.reason, "session_limit")
        self.assertEqual(
            sum(ScoutPreviewAccess.objects.filter(
                session_digest=session_digest("bounded-session")
            ).values_list("enqueue_count", flat=True)),
            5,
        )

    @override_settings(SCOUT_PREVIEW_LEASE_SECONDS=6, SCOUT_PREVIEW_HARD_TIMEOUT_SECONDS=1)
    def test_worker_enforces_hard_deadline_and_requeues_retry(self):
        task, _ = enqueue_preview(INTENT, "session-one")
        task_id, token = claim_task()

        with patch("jobs.services.scout_tasks.compute_task", side_effect=lambda *_: time.sleep(2)):
            self.assertFalse(execute_claimed_task(task_id, token))

        task.refresh_from_db()
        self.assertEqual(task.state, ScoutPreviewTask.State.QUEUED)
        self.assertEqual(task.last_error, "TimeoutError")


class ScoutPreviewWorkerReleaseGateTests(TestCase):
    def test_release_state_gate_allows_only_the_active_release_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            state_file.write_text(json.dumps({"active": {"release": "release-2"}}), encoding="utf-8")
            with mock.patch.dict(os.environ, {
                "SCOUT_PREVIEW_ACTIVE_STATE_FILE": str(state_file), "RELEASE_ID": "release-2"
            }):
                self.assertTrue(release_is_active())
            with mock.patch.dict(os.environ, {
                "SCOUT_PREVIEW_ACTIVE_STATE_FILE": str(state_file), "RELEASE_ID": "release-1"
            }):
                self.assertFalse(release_is_active())
            with mock.patch.dict(os.environ, {
                "SCOUT_PREVIEW_ACTIVE_STATE_FILE": str(state_file) + ".missing", "RELEASE_ID": "release-2"
            }):
                self.assertFalse(release_is_active())


class ScoutPreviewFingerprintTests(TestCase):
    def test_raw_source_change_invalidates_saved_match_annotations(self):
        setup_scout_fixture(self)
        old_fingerprint = job_fingerprint(self.job)
        row = {
            "job_id": self.job.pk,
            "fingerprint": old_fingerprint,
            "score": 88,
            "reasons": ["Check: confirm source details"],
            "needs_confirmation": True,
            "close_match": False,
            "caveats": [],
        }
        type(self.job).objects.filter(pk=self.job.pk).update(raw_data={"salaryPeriod": "hour"})

        jobs, older_jobs, stale = hydrate_result({"matches": [row], "older_jobs": []})

        self.assertTrue(stale)
        self.assertEqual(jobs, [])
        self.assertEqual(older_jobs, [])

    def test_unavailable_saved_match_invalidates_snapshot(self):
        setup_scout_fixture(self)
        row = {
            "job_id": self.job.pk,
            "fingerprint": job_fingerprint(self.job),
            "score": 88,
            "reasons": ["Python role"],
            "needs_confirmation": False,
            "close_match": False,
            "caveats": [],
        }
        type(self.job).objects.filter(pk=self.job.pk).update(is_active=False)

        jobs, older_jobs, stale = hydrate_result({"matches": [row], "older_jobs": []})

        self.assertTrue(stale)
        self.assertEqual(jobs, [])
        self.assertEqual(older_jobs, [])


class ScoutPreviewPruneTests(TestCase):
    def test_prune_keeps_recently_refreshed_queue_and_removes_abandoned_and_old_terminal(self):
        now = timezone.now()
        old = now - timedelta(hours=25)
        refreshed, _ = enqueue_preview("Recently refreshed queued task", "prune-session")
        ScoutPreviewTask.objects.filter(pk=refreshed.pk).update(
            created_at=old, updated_at=now, available_at=now
        )
        abandoned = ScoutPreviewTask.objects.create(
            semantic_key=semantic_key("Abandoned old queued task"),
            intent_text="Abandoned old queued task",
            available_at=old,
        )
        ScoutPreviewTask.objects.filter(pk=abandoned.pk).update(created_at=old, updated_at=old)
        terminal = ScoutPreviewTask.objects.create(
            semantic_key=semantic_key("Old completed task"),
            intent_text="Old completed task",
            state=ScoutPreviewTask.State.SUCCEEDED,
            available_at=old,
            completed_at=old,
        )
        ScoutPreviewTask.objects.filter(pk=terminal.pk).update(created_at=old, updated_at=now)

        deleted = prune_tasks()

        self.assertEqual(deleted, 2)
        self.assertTrue(ScoutPreviewTask.objects.filter(pk=refreshed.pk).exists())
        self.assertFalse(ScoutPreviewTask.objects.filter(pk=abandoned.pk).exists())
        self.assertFalse(ScoutPreviewTask.objects.filter(pk=terminal.pk).exists())


@skipUnlessDBFeature("has_select_for_update")
class ScoutPreviewPostgresConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def _race(self, calls):
        barrier = threading.Barrier(len(calls))
        results = []
        errors = []

        def run(call):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                results.append(call())
            except Exception as exc:  # noqa: BLE001 - assert thread failures in the test thread
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=run, args=(call,)) for call in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads), "concurrent task operation hung")
        self.assertEqual(errors, [])
        return results

    def test_concurrent_requests_cannot_exceed_global_queue_limit(self):
        def bounded_enqueue(intent, session):
            try:
                return enqueue_preview(intent, session)
            except PreviewQueueLimited as exc:
                return exc

        with override_settings(SCOUT_PREVIEW_QUEUE_LIMIT=1):
            results = self._race([
                lambda: bounded_enqueue(INTENT, "pg-session-one"),
                lambda: bounded_enqueue("Remote impact data science roles in Canada.", "pg-session-two"),
            ])
        limited = [result for result in results if isinstance(result, PreviewQueueLimited)]
        accepted = [result for result in results if isinstance(result, tuple)]
        self.assertEqual(len(limited), 1)
        self.assertEqual(limited[0].reason, "queue_full")
        self.assertEqual(len(accepted), 1)
        self.assertTrue(accepted[0][1])
        self.assertEqual(ScoutPreviewQueueGuard.objects.count(), 1)
        self.assertEqual(ScoutPreviewTask.objects.filter(state=ScoutPreviewTask.State.QUEUED).count(), 1)

    def test_competing_workers_lease_a_task_only_once(self):
        task, _ = enqueue_preview(INTENT, "pg-session-one")
        claims = self._race([claim_task, claim_task])
        claimed = [claim for claim in claims if claim is not None]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0][0], task.pk)


@override_settings(SCOUT_PREVIEW_ASYNC_ENABLED=True)
class ScoutPreviewAsyncViewTests(TestCase):
    def setUp(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider,
                provider_id=provider_id,
                name=(provider_id or provider).title(),
                client_id="test-client",
                secret="test-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(site)

    def start(self, client, intent=INTENT, **fields):
        return client.post(reverse("jobs:scout_preview_start"), {"intent": intent, **fields},
                           HTTP_ACCEPT="application/json")

    def succeed(self, task, diagnostics=None):
        task.state = ScoutPreviewTask.State.SUCCEEDED
        task.result = {"preferences": {}, "matches": [], "older_jobs": [], "diagnostics": diagnostics or {}}
        task.completed_at = timezone.now()
        task.save(update_fields=["state", "result", "completed_at"])

    def test_plain_get_shows_progress_but_starts_no_paid_search(self):
        browser_like = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36"
        with patch("jobs.services.job_scout_service.JobScoutService.search_intent") as search:
            response = Client().get(reverse("jobs:job_scout_preview"), {"intent": INTENT}, HTTP_USER_AGENT=browser_like)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["preview_pending"])
        self.assertContains(response, "Checking the current catalogue")
        self.assertContains(response, "Show my matches")
        search.assert_not_called()
        self.assertFalse(ScoutPreviewTask.objects.exists())
        self.assertFalse(ConsumerFunnelEvent.objects.filter(event=ConsumerFunnelEvent.Event.SCOUT_PREVIEWED).exists())

    def test_page_script_start_queues_one_search_and_the_page_then_polls(self):
        first_client = Client()
        other_client = Client()
        first_client.get(reverse("jobs:job_scout_preview"), {"intent": INTENT})
        with patch("jobs.services.job_scout_service.JobScoutService.search_intent") as search:
            started = self.start(first_client)
            again = self.start(first_client)

        search.assert_not_called()
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["status"], "processing")
        self.assertEqual(again.json(), started.json())
        task = ScoutPreviewTask.objects.get()
        status_url = reverse("jobs:scout_preview_status", kwargs={"task_id": task.pk})
        self.assertEqual(started.json()["status_url"], status_url)
        self.assertEqual(first_client.get(status_url).status_code, 202)
        self.assertEqual(other_client.get(status_url).status_code, 404)

        page = first_client.get(reverse("jobs:job_scout_preview"), {"intent": INTENT})
        self.assertTrue(page.context["preview_pending"])
        self.assertFalse(page.context["preview_start"])
        self.assertEqual(page.context["preview_status_url"], status_url)

    def test_start_requires_the_page_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("jobs:job_scout_preview"), {"intent": INTENT})
        response = self.start(client)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ScoutPreviewTask.objects.exists())

    def test_button_without_script_starts_the_search_and_returns_to_the_page(self):
        client = Client()
        response = client.post(
            reverse("jobs:scout_preview_start"),
            {"intent": INTENT, "source": "job_detail", "scout_flow": "flow-1"},
            follow=True,
        )

        self.assertEqual(len(response.redirect_chain), 1)
        self.assertIn("source=job_detail", response.redirect_chain[0][0])
        self.assertTrue(response.context["preview_pending"])
        self.assertFalse(response.context["preview_start"])
        self.assertEqual(ScoutPreviewTask.objects.get().state, ScoutPreviewTask.State.QUEUED)

    def test_full_queue_tells_the_page_scout_is_busy(self):
        with override_settings(SCOUT_PREVIEW_QUEUE_LIMIT=0):
            response = self.start(Client())
            page = Client().post(reverse("jobs:scout_preview_start"), {"intent": INTENT}, follow=True)

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json(), {"status": "busy"})
        self.assertContains(page, "Scout is busy right now")
        self.assertFalse(ScoutPreviewTask.objects.exists())

    def test_malformed_task_hint_does_not_break_preview_request(self):
        response = Client().get(
            reverse("jobs:job_scout_preview"),
            {"intent": INTENT, "scout_task": "not-a-uuid"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["preview_pending"])

    def test_completed_result_is_reused_by_the_same_session(self):
        client = Client()
        self.start(client)
        self.succeed(ScoutPreviewTask.objects.get())

        response = client.get(reverse("jobs:job_scout_preview"), {"intent": INTENT})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["preview_pending"])
        self.assertEqual(ScoutPreviewTask.objects.count(), 1)

    def test_a_fresh_result_renders_on_plain_get_without_a_new_search(self):
        self.start(Client())
        task = ScoutPreviewTask.objects.get()
        self.succeed(task)

        response = Client().get(reverse("jobs:job_scout_preview"), {"intent": INTENT})

        self.assertFalse(response.context["preview_pending"])
        self.assertNotContains(response, "Show my matches")
        self.assertEqual(ScoutPreviewTask.objects.count(), 1)
        self.assertEqual(ScoutPreviewAccess.objects.filter(task=task).count(), 2)
        self.assertEqual(sum(ScoutPreviewAccess.objects.values_list("enqueue_count", flat=True)), 1)
        self.assertTrue(ConsumerFunnelEvent.objects.filter(event=ConsumerFunnelEvent.Event.SCOUT_PREVIEWED).exists())

    def test_expired_result_waits_for_the_page_to_start_a_refresh(self):
        client = Client()
        self.start(client)
        task = ScoutPreviewTask.objects.get()
        self.succeed(task)
        ScoutPreviewTask.objects.filter(pk=task.pk).update(completed_at=timezone.now() - timedelta(hours=1))

        page = client.get(reverse("jobs:job_scout_preview"), {"intent": INTENT})
        task.refresh_from_db()
        self.assertTrue(page.context["preview_start"])
        self.assertEqual(task.state, ScoutPreviewTask.State.SUCCEEDED)

        self.assertEqual(self.start(client).json()["status"], "processing")
        task.refresh_from_db()
        self.assertEqual(task.state, ScoutPreviewTask.State.QUEUED)

    def test_failed_page_reload_stays_final_until_user_explicitly_retries(self):
        client = Client()
        url = reverse("jobs:job_scout_preview")
        self.start(client)
        task = ScoutPreviewTask.objects.get()
        task.state = ScoutPreviewTask.State.FAILED
        task.last_error = "TimeoutError"
        task.completed_at = timezone.now()
        task.save(update_fields=["state", "last_error", "completed_at"])

        reloaded = client.get(url, {"intent": INTENT})

        self.assertEqual(reloaded.status_code, 200)
        self.assertFalse(reloaded.context["preview_pending"])
        self.assertTrue(reloaded.context["review_incomplete"])
        self.assertContains(reloaded, reverse("jobs:scout_preview_start"))
        self.assertEqual(self.start(client).json()["status"], "retry")
        task.refresh_from_db()
        self.assertEqual(task.state, ScoutPreviewTask.State.FAILED)

        retried = client.post(reverse("jobs:scout_preview_start"), {"intent": INTENT, "retry": "1"}, follow=True)

        self.assertEqual(retried.status_code, 200)
        self.assertEqual(len(retried.redirect_chain), 1)
        self.assertNotIn("retry=1", retried.redirect_chain[0][0])
        self.assertTrue(retried.context["preview_pending"])
        task.refresh_from_db()
        self.assertEqual(task.state, ScoutPreviewTask.State.QUEUED)
        self.assertEqual(ScoutPreviewAccess.objects.get(task=task).enqueue_count, 2)

        client.get(url, {"intent": INTENT})
        task.refresh_from_db()
        self.assertEqual(task.state, ScoutPreviewTask.State.QUEUED)
        self.assertEqual(ScoutPreviewAccess.objects.get(task=task).enqueue_count, 2)

    def test_explicit_retry_refreshes_completed_incomplete_result_once(self):
        client = Client()
        url = reverse("jobs:job_scout_preview")
        self.start(client)
        task = ScoutPreviewTask.objects.get()
        self.succeed(task, {"review_incomplete": True})

        retried = client.post(reverse("jobs:scout_preview_start"), {"intent": INTENT, "retry": "1"}, follow=True)

        self.assertEqual(retried.status_code, 200)
        self.assertEqual(len(retried.redirect_chain), 1)
        self.assertNotIn("retry=1", retried.redirect_chain[0][0])
        self.assertTrue(retried.context["preview_pending"])
        task.refresh_from_db()
        self.assertEqual(task.state, ScoutPreviewTask.State.QUEUED)
        self.assertEqual(ScoutPreviewAccess.objects.get(task=task).enqueue_count, 2)

        client.get(url, {"intent": INTENT})
        task.refresh_from_db()
        self.assertEqual(task.state, ScoutPreviewTask.State.QUEUED)
        self.assertEqual(ScoutPreviewAccess.objects.get(task=task).enqueue_count, 2)
