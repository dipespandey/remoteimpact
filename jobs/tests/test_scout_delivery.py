from io import StringIO
from threading import Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from jobs.management.commands.send_job_alerts import Command
from jobs.models import AssistantSubscription, Job, JobAlert, JobAlertDelivery, Organization
from jobs.services.email_service import email_service


class ScoutDeliveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="delivery", email="delivery@example.com")
        AssistantSubscription.objects.create(user=self.user, is_subscribed=True)
        self.org = Organization.objects.create(name="Climate Test", slug="climate-test-delivery")
        self.job = Job.objects.create(title="Python Engineer", slug="delivery-python", organization=self.org)
        self.alert = JobAlert.objects.create(user=self.user, name="My Job Scout", intent_text="Remote Python jobs in Europe", frequency="instant")

    def run_command(self, **kwargs):
        call_command("send_job_alerts", frequency="instant", stdout=StringIO(), **kwargs)

    @patch("jobs.services.embedding_service.load_for_batch")
    @patch.object(JobAlert, "get_matching_jobs")
    @patch.object(email_service, "send_job_alert", return_value=True)
    def test_forced_repeat_cannot_resend_a_stale_or_duplicated_candidate(self, send, matches, load):
        matches.return_value = [self.job, self.job]
        self.run_command(force=True)
        self.run_command(force=True)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(len(send.call_args.args[1]), 1)
        self.assertEqual(JobAlertDelivery.objects.filter(alert=self.alert).count(), 1)

    @patch("jobs.services.embedding_service.load_for_batch")
    @patch("jobs.services.job_stats.record", side_effect=RuntimeError("metrics unavailable"))
    @patch.object(JobAlert, "get_matching_jobs")
    @patch.object(email_service, "send_job_alert", return_value=True)
    def test_metric_failure_does_not_lose_receipt_or_trigger_resend(self, send, matches, record, load):
        matches.return_value = [self.job]
        self.run_command(force=True)
        self.alert.refresh_from_db()
        self.assertIsNotNone(self.alert.last_sent_at)
        self.run_command(force=True)
        self.assertEqual(send.call_count, 1)

    @patch.object(JobAlert, "get_matching_jobs")
    @patch.object(email_service, "send_job_alert", return_value=False)
    def test_provider_failure_does_not_mark_jobs_delivered(self, send, matches):
        matches.return_value = [self.job]
        outcome, _ = Command()._process_alert(self.alert.pk, ["instant"], dry_run=False, force=True)
        self.assertEqual(outcome, "error")
        self.assertFalse(self.alert.deliveries.exists())
        self.alert.refresh_from_db()
        self.assertIsNone(self.alert.last_sent_at)

    @patch.object(JobAlert, "get_matching_jobs")
    @patch.object(email_service, "send_job_alert")
    def test_dry_run_is_read_only_for_delivery_state(self, send, matches):
        matches.return_value = [self.job]
        command = Command(stdout=StringIO())
        command._process_alert(self.alert.pk, ["instant"], dry_run=True, force=True)
        send.assert_not_called()
        self.assertFalse(self.alert.deliveries.exists())
        self.alert.refresh_from_db()
        self.assertIsNone(self.alert.last_sent_at)

    @patch.object(JobAlert, "get_matching_jobs")
    @patch.object(email_service, "send_job_alert")
    def test_worker_rechecks_timestamp_after_candidate_selection(self, send, matches):
        alert_id = self.alert.pk
        JobAlert.objects.filter(pk=alert_id).update(last_sent_at=timezone.now())
        outcome, _ = Command()._process_alert(alert_id, ["instant"], dry_run=False, force=False)
        self.assertEqual(outcome, "skipped")
        matches.assert_not_called()
        send.assert_not_called()

    @patch.object(JobAlert, "get_matching_jobs")
    @patch.object(email_service, "send_job_alert")
    def test_worker_rechecks_pause_and_frequency(self, send, matches):
        JobAlert.objects.filter(pk=self.alert.pk).update(is_active=False)
        outcome, _ = Command()._process_alert(self.alert.pk, ["instant"], dry_run=False, force=True)
        self.assertEqual(outcome, "skipped")
        JobAlert.objects.filter(pk=self.alert.pk).update(is_active=True, frequency="once")
        outcome, _ = Command()._process_alert(self.alert.pk, ["instant"], dry_run=False, force=True)
        self.assertEqual(outcome, "skipped")
        send.assert_not_called()

    @patch.object(email_service, "send_email", return_value=True)
    def test_same_default_names_get_distinct_search_and_job_subjects(self, send):
        other = JobAlert.objects.create(user=self.user, name="My Job Scout", intent_text="Remote research fellowships", frequency="instant")
        email_service.send_job_alert(self.alert, [self.job])
        first = send.call_args.kwargs
        email_service.send_job_alert(other, [self.job])
        second = send.call_args.kwargs
        self.assertNotEqual(first["subject"], second["subject"])
        self.assertIn("Remote Python jobs in Europe", first["subject"])
        self.assertIn("Python Engineer", first["subject"])
        self.assertIn("Remote research fellowships", second["subject"])
        self.assertIn("more than one email a day", first["html"])
        self.assertIn("more than one email a day", first["text"])
        self.assertIn("&source=scout_email_feedback", first["text"])
        self.assertNotIn("&amp;source=scout_email_feedback", first["text"])

    @patch.object(email_service, "send_email", return_value=True)
    def test_custom_name_is_preserved_and_control_characters_are_removed(self, send):
        self.alert.name = "Climate\r\nEngineering"
        email_service.send_job_alert(self.alert, [self.job])
        self.assertIn("Climate Engineering", send.call_args.kwargs["subject"])
        self.assertNotIn("\n", send.call_args.kwargs["subject"])


class ScoutDeliveryLockTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update_skip_locked")
    @patch.object(email_service, "send_job_alert")
    def test_overlapping_worker_skips_alert_locked_by_another_worker(self, send):
        user = get_user_model().objects.create_user(username="locked-scout", email="locked@example.com")
        alert = JobAlert.objects.create(user=user, frequency="instant")
        results = []

        def worker():
            try:
                results.append(Command()._process_alert(alert.pk, ["instant"], dry_run=False, force=True))
            except Exception as exc:
                results.append(exc)
            finally:
                connections.close_all()

        with transaction.atomic():
            JobAlert.objects.select_for_update().get(pk=alert.pk)
            thread = Thread(target=worker)
            thread.start()
            thread.join(timeout=5)
            still_running = thread.is_alive()
        thread.join(timeout=5)
        self.assertFalse(still_running, "Competing worker should skip, not wait for the locked row")
        self.assertEqual(results, [("skipped", [])])
        send.assert_not_called()
