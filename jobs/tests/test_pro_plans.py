from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import AssistantSubscription, ConsumerFunnelEvent
from jobs.services.payment_service import PaymentService

User = get_user_model()


def _session(**kwargs):
    defaults = dict(
        id="cs_plan",
        url="https://checkout.example/plan",
        expires_at=int((timezone.now() + timedelta(days=1)).timestamp()),
    )
    defaults.update(kwargs)
    return Mock(**defaults)


@override_settings(STRIPE_SECRET_KEY="sk_test_example")
class ProPlanCheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="plans", email="plans@example.com", password="test-pass-123")
        AssistantSubscription.objects.create(user=self.user, stripe_customer_id="cus_plans")

    @patch("jobs.services.payment_service.stripe.checkout.Session.create")
    def test_monthly_offers_trial_to_first_time_subscribers(self, create):
        create.return_value = _session()
        PaymentService.create_pro_subscription_session(self.user, "https://remoteimpact.org", plan="monthly")
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["mode"], "subscription")
        self.assertEqual(kwargs["subscription_data"]["trial_period_days"], 7)
        self.assertEqual(kwargs["metadata"]["plan"], "monthly")
        self.assertNotIn("payment_method_types", kwargs)

    @patch("jobs.services.payment_service.stripe.checkout.Session.create")
    def test_returning_subscriber_gets_no_second_trial(self, create):
        AssistantSubscription.objects.filter(user=self.user).update(subscribed_at=timezone.now() - timedelta(days=90))
        create.return_value = _session()
        PaymentService.create_pro_subscription_session(self.user, "https://remoteimpact.org", plan="monthly")
        self.assertNotIn("trial_period_days", create.call_args.kwargs["subscription_data"])

    @patch("jobs.services.payment_service.stripe.checkout.Session.create")
    def test_pass_is_a_one_time_payment(self, create):
        create.return_value = _session()
        PaymentService.create_pro_subscription_session(self.user, "https://remoteimpact.org", plan="pass")
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["mode"], "payment")
        self.assertNotIn("subscription_data", kwargs)
        self.assertNotIn("recurring", kwargs["line_items"][0]["price_data"])
        self.assertEqual(kwargs["line_items"][0]["price_data"]["unit_amount"], 2400)
        self.assertEqual(kwargs["metadata"]["plan"], "pass")

    @patch("jobs.services.payment_service.stripe.checkout.Session.create")
    @patch("jobs.services.payment_service.stripe.checkout.Session.retrieve")
    def test_open_session_for_other_plan_is_not_reused(self, retrieve, create):
        AssistantSubscription.objects.filter(user=self.user).update(stripe_checkout_session_id="cs_monthly_open")
        retrieve.return_value = Mock(status="open", url="https://checkout.example/monthly", metadata={"plan": "monthly"})
        create.return_value = _session(id="cs_pass")
        url = PaymentService.create_pro_subscription_session(self.user, "https://remoteimpact.org", plan="pass")
        self.assertEqual(url, "https://checkout.example/plan")
        create.assert_called_once()

    @patch("jobs.views.job_alerts.PaymentService.create_pro_subscription_session", return_value=_session())
    def test_view_passes_selected_plan(self, create):
        self.client.force_login(self.user)
        self.client.post(reverse("jobs:job_scout_subscribe"), {"source": "pricing", "plan": "pass"})
        self.assertEqual(create.call_args.kwargs["plan"], "pass")
        self.client.post(reverse("jobs:job_scout_subscribe"), {"source": "pricing", "plan": "bogus"})
        self.assertEqual(create.call_args.kwargs["plan"], "monthly")


class ProPassWebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="passer", email="passer@example.com", password="test-pass-123")
        self.subscription = AssistantSubscription.objects.create(user=self.user)

    def _event(self, session_id="cs_pass_paid"):
        return {
            "id": f"evt_{session_id}",
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": session_id,
                "mode": "payment",
                "payment_status": "paid",
                "customer": "cus_pass",
                "metadata": {"user_id": str(self.user.id), "plan": "pass", "source": "pricing"},
            }},
        }

    def test_pass_grants_ninety_days_once(self):
        PaymentService.handle_webhook_event(self._event())
        PaymentService.handle_webhook_event(self._event())  # Stripe retry
        self.subscription.refresh_from_db()
        self.assertTrue(self.subscription.is_pro_active())
        remaining = self.subscription.expires_at - timezone.now()
        self.assertGreater(remaining, timedelta(days=89))
        self.assertLess(remaining, timedelta(days=91))
        self.assertEqual(self.subscription.stripe_customer_id, "cus_pass")
        self.assertEqual(
            ConsumerFunnelEvent.objects.filter(event=ConsumerFunnelEvent.Event.PAYMENT_COMPLETED, user=self.user).count(), 1
        )

    def test_second_pass_extends_from_current_expiry(self):
        PaymentService.handle_webhook_event(self._event("cs_first"))
        PaymentService.handle_webhook_event(self._event("cs_second"))
        self.subscription.refresh_from_db()
        self.assertGreater(self.subscription.expires_at - timezone.now(), timedelta(days=179))

    @patch("jobs.services.payment_service.stripe.checkout.Session.retrieve")
    @override_settings(STRIPE_SECRET_KEY="sk_test_example")
    def test_return_visit_and_webhook_do_not_double_grant(self, retrieve):
        retrieve.return_value = Mock(
            id="cs_both", payment_status="paid", subscription=None, customer="cus_pass",
            metadata={"user_id": str(self.user.id), "plan": "pass"},
        )
        ok, _ = PaymentService.verify_assistant_subscription("cs_both", self.user)
        self.assertTrue(ok)
        PaymentService.handle_webhook_event(self._event("cs_both"))
        self.subscription.refresh_from_db()
        self.assertLess(self.subscription.expires_at - timezone.now(), timedelta(days=91))


class ProTrialTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="trialer", email="trialer@example.com", password="test-pass-123")
        self.subscription = AssistantSubscription.objects.create(user=self.user, stripe_subscription_id="sub_trial")

    @patch("jobs.services.payment_service.stripe.checkout.Session.retrieve")
    @override_settings(STRIPE_SECRET_KEY="sk_test_example")
    def test_trial_checkout_return_activates_pro(self, retrieve):
        retrieve.return_value = Mock(
            id="cs_trial", payment_status="no_payment_required", subscription="sub_trial",
            metadata={"user_id": str(self.user.id), "plan": "monthly"},
        )
        ok, subscription = PaymentService.verify_assistant_subscription("cs_trial", self.user)
        self.assertTrue(ok)
        self.assertTrue(subscription.is_pro_active())

    @patch("jobs.services.email_service.email_service.send_email", return_value=True)
    def test_trial_will_end_sends_one_reminder(self, send_email):
        trial_end = int((timezone.now() + timedelta(days=3)).timestamp())
        event = {
            "id": "evt_trial_end",
            "type": "customer.subscription.trial_will_end",
            "data": {"object": {"id": "sub_trial", "trial_end": trial_end, "metadata": {"user_id": str(self.user.id)}}},
        }
        send_email.reset_mock()
        PaymentService.handle_webhook_event(event)
        PaymentService.handle_webhook_event(event)
        self.assertEqual(send_email.call_count, 1)
        self.assertIn("trial ends soon", send_email.call_args.kwargs["subject"])
        self.assertTrue(ConsumerFunnelEvent.objects.filter(event=ConsumerFunnelEvent.Event.TRIAL_REMINDER_SENT).exists())


class TrialReminderCommandTests(TestCase):
    @patch("jobs.services.email_service.email_service.send_email", return_value=True)
    @patch("jobs.management.commands.send_pro_trial_reminders.stripe.Subscription.retrieve")
    def test_command_reminds_only_trialing_subscribers_once(self, retrieve, send_email):
        from django.core.management import call_command

        user = User.objects.create_user(username="cmdtrial", email="cmdtrial@example.com", password="test-pass-123")
        AssistantSubscription.objects.create(
            user=user, is_subscribed=True, stripe_subscription_id="sub_cmd",
            expires_at=timezone.now() + timedelta(days=2),
        )
        other = User.objects.create_user(username="cmdpaid", email="cmdpaid@example.com", password="test-pass-123")
        AssistantSubscription.objects.create(
            user=other, is_subscribed=True, stripe_subscription_id="sub_paid",
            expires_at=timezone.now() + timedelta(days=2),
        )
        trial_end = int((timezone.now() + timedelta(days=2)).timestamp())
        retrieve.side_effect = lambda sub_id: {
            "sub_cmd": {"id": "sub_cmd", "status": "trialing", "trial_end": trial_end},
            "sub_paid": {"id": "sub_paid", "status": "active", "trial_end": None},
        }[sub_id]
        send_email.reset_mock()
        call_command("send_pro_trial_reminders")
        call_command("send_pro_trial_reminders")
        reminders = [c for c in send_email.call_args_list if "trial ends soon" in c.kwargs["subject"]]
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0].kwargs["to"], "cmdtrial@example.com")
