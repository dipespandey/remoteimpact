from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.models import (AssistantSubscription, Category, ConsumerFunnelEvent, Job, JobAlert, JobAlertDelivery,
                         Organization)

User = get_user_model()
CE = ConsumerFunnelEvent.Event


class PlanPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="seeker", email="seeker@example.com", password="pw-123456!")
        AssistantSubscription.objects.create(user=self.user)
        self.client.force_login(self.user)
        org = Organization.objects.create(name="Useful Work")
        category = Category.objects.create(name="Climate", slug="climate")
        self.job = Job.objects.create(title="Analyst", slug="analyst", organization=org, category=category,
                                      description="d", posted_at=timezone.now())

    def test_zero_match_scout_is_told_to_fix_brief(self):
        JobAlert.objects.create(user=self.user, name="Narrow")
        html = self.client.get(reverse("jobs:job_scout_subscribe"), secure=True).content.decode()
        self.assertIn("Fix my brief for free", html)
        self.assertIn("Or start Pro now", html)

    def test_no_scout_is_told_to_create_one(self):
        html = self.client.get(reverse("jobs:job_scout_subscribe"), secure=True).content.decode()
        self.assertIn("Create my free Scout", html)

    def test_scout_with_matches_sees_plans_only(self):
        alert = JobAlert.objects.create(user=self.user, name="Good")
        JobAlertDelivery.objects.create(alert=alert, job=self.job)
        html = self.client.get(reverse("jobs:job_scout_subscribe"), secure=True).content.decode()
        self.assertNotIn("Fix my brief for free", html)
        self.assertIn("match", html)


class PlanPageRecoveryTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Useful Work")
        category = Category.objects.create(name="Climate", slug="climate")
        self.job = Job.objects.create(title="Analyst", slug="analyst", organization=org, category=category,
                                      description="d", posted_at=timezone.now())

    def _viewer(self, name, matches):
        user = User.objects.create_user(username=name, email=f"{name}@example.com", password="pw-123456!")
        alert = JobAlert.objects.create(user=user, name="Scout")
        if matches:
            JobAlertDelivery.objects.create(alert=alert, job=self.job)
        event = ConsumerFunnelEvent.objects.create(user=user, event=CE.UPGRADE_CLICKED, metadata={"source": "x"})
        ConsumerFunnelEvent.objects.filter(pk=event.pk).update(created_at=timezone.now() - timedelta(hours=5))
        return user

    @patch("jobs.services.email_service.email_service.send_checkout_recovery", return_value=True)
    def test_emails_plan_page_viewers_with_matches_once(self, send):
        with_matches = self._viewer("hasmatches", True)
        self._viewer("nomatches", False)
        call_command("send_checkout_recovery", "--send", stdout=StringIO())
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[0], with_matches)
        self.assertFalse(send.call_args.kwargs["reached_checkout"])
        call_command("send_checkout_recovery", "--send", stdout=StringIO())
        self.assertEqual(send.call_count, 1)

    def test_plan_page_email_copy(self):
        from django.core import mail
        user = self._viewer("copy", True)
        from jobs.services.email_service import email_service
        with patch.object(email_service, "send_email", return_value=True) as send_email:
            email_service.send_checkout_recovery(user, trial=True, resume_url="https://x", reached_checkout=False)
        self.assertIn("had a look at Remote Impact Pro", send_email.call_args.kwargs["html"])
