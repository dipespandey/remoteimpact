import json
from io import StringIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from growth.management.commands.scout_quality_report import classify
from jobs.models import ConsumerFunnelEvent as E, JobAlert


class ScoutQualityTests(TestCase):
    def test_unique_scouts_first_outcomes_retries_and_internal_exclusion(self):
        user = get_user_model().objects.create_user("scout-user")
        internal = get_user_model().objects.create_user("internal", is_staff=True)
        alert = JobAlert.objects.create(user=user, intent_text="DO NOT EXPORT PRIVATE BRIEF")
        E.objects.create(user=user, alert=alert, event=E.Event.INITIAL_MATCHES_EMPTY,
                         metadata={"matching": {"review_incomplete": True, "hard_rejections": {"location": 22, "private-key@example.org": 1}}})
        E.objects.create(user=user, alert=alert, event=E.Event.INITIAL_EMAIL_SENT)
        legacy = JobAlert.objects.create(user=user)
        E.objects.create(user=user, alert=legacy, event=E.Event.INITIAL_MATCHES_EMPTY)
        JobAlert.objects.create(user=internal)
        out = StringIO()
        with patch("jobs.services.job_scout_service.JobScoutService.get_matches", side_effect=AssertionError("No matching in report")):
            call_command("scout_quality_report", stdout=out)
        result = json.loads(out.getvalue())
        self.assertEqual(result["scouts"], 2)
        self.assertEqual(result["first_outcomes"], {"review_incomplete": 1, "empty_without_diagnostics": 1})
        self.assertEqual(result["latest_outcomes"]["email_send_accepted"], 1)
        self.assertEqual(result["scouts_with_repeated_outcome_events"], 1)
        self.assertEqual(result["scouts_with_recorded_hard_rejections"], {"location": 1})
        self.assertEqual(result["excluded_known_internal_scouts"], 1)
        self.assertNotIn("PRIVATE BRIEF", out.getvalue())
        self.assertNotIn("private-key", out.getvalue())

    def test_transport_failure_is_not_an_empty_match(self):
        event = E(event=E.Event.INITIAL_EMAIL_FAILED, metadata={"matching": {"match_count": 5}})
        self.assertEqual(classify(event), "email_transport_failed")
