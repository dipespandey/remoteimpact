from datetime import timedelta
from unittest import mock

import stripe

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from jobs.models import Category, EmployerFunnelEvent, Job, Organization, OrgSubscription
from jobs.services import org_plans
from jobs.services.payment_service import PaymentService


class OrgPlanTests(TestCase):
    def setUp(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider, client_id="x", secret="y").sites.add(Site.objects.get_current())
        User = get_user_model()
        self.owner = User.objects.create_user("owner", "owner@org.example", "pw")
        self.outsider = User.objects.create_user("out", "out@else.example", "pw")
        self.org = Organization.objects.create(name="Big Org", slug="big-org", website="https://bigorg.example/")
        self.org.members.add(self.owner)
        cat = Category.objects.create(name="Impact", slug="impact")
        now = timezone.now()
        self.jobs = [Job.objects.create(
            title=f"Role {i}", slug=f"role-{i}", organization=self.org, category=cat, description="d " * 40, location="Remote",
            job_type="full-time", source="feed", is_active=True, posted_at=now - timedelta(days=i), last_seen_at=now) for i in range(8)]
        self.stripe_subscription = mock.patch("jobs.services.org_plans.stripe.Subscription.retrieve")
        self.retrieve = self.stripe_subscription.start()
        self.addCleanup(self.stripe_subscription.stop)

    def session(self, tier="pro"):
        self.retrieve.return_value = {
            "id": "sub_1", "status": "active", "customer": "cus_1",
            "current_period_end": int((timezone.now() + timedelta(days=30)).timestamp()),
            "metadata": {"product": "org_plan", "organization_id": str(self.org.pk), "tier": tier}}
        return {"type": "checkout.session.completed", "id": "evt_1", "data": {"object": {
            "id": "cs_1", "mode": "subscription", "payment_status": "paid", "customer": "cus_1", "subscription": "sub_1",
            "metadata": {"product": "org_plan", "organization_id": str(self.org.pk), "tier": tier}}}}

    def test_checkout_activates_plan_features_newest_roles_up_to_cap_and_notifies(self):
        OrgSubscription.objects.create(organization=self.org, stripe_checkout_session_id="cs_1", stripe_customer_id="cus_1")
        PaymentService.handle_webhook_event(self.session("pro"))
        sub = OrgSubscription.objects.get(organization=self.org)
        self.assertEqual((sub.tier, sub.stripe_subscription_id), ("pro", "sub_1"))
        featured = set(Job.objects.filter(featured_until__gt=timezone.now()).values_list("slug", flat=True))
        self.assertEqual(featured, {f"role-{i}" for i in range(5)})  # the 5 newest of 8
        self.assertTrue(EmployerFunnelEvent.objects.filter(event="payment_completed", metadata__product="org_plan").exists())
        # replaying the same event does not duplicate the payment event
        PaymentService.handle_webhook_event(self.session("pro"))
        self.assertEqual(EmployerFunnelEvent.objects.filter(event="payment_completed", metadata__product="org_plan").count(), 1)

    def test_larger_plan_features_everything_and_never_shortens_a_longer_paid_window(self):
        OrgSubscription.objects.create(organization=self.org, stripe_checkout_session_id="cs_1", stripe_customer_id="cus_1")
        long_until = timezone.now() + timedelta(days=25)
        Job.objects.filter(slug="role-7").update(featured_until=long_until)
        PaymentService.handle_webhook_event(self.session("growth"))
        self.assertEqual(Job.objects.filter(featured_until__gt=timezone.now()).count(), 8)
        self.assertEqual(Job.objects.get(slug="role-7").featured_until, long_until)

    def test_cancellation_ends_the_plan_and_stops_featuring(self):
        OrgSubscription.objects.create(organization=self.org, stripe_checkout_session_id="cs_1", stripe_customer_id="cus_1")
        PaymentService.handle_webhook_event(self.session("pro"))
        PaymentService.handle_webhook_event({"type": "customer.subscription.deleted", "id": "evt_2", "data": {"object": {
            "id": "sub_1", "metadata": {"product": "org_plan", "organization_id": str(self.org.pk), "tier": "pro"}}}})
        sub = OrgSubscription.objects.get(organization=self.org)
        self.assertEqual(sub.tier, "free")
        self.assertFalse(org_plans.is_active(sub))
        before = Job.objects.filter(featured_until__gt=timezone.now() + timedelta(days=3)).count()
        self.assertEqual(org_plans.sync_featuring(), 0)  # nothing refreshed once the plan has ended
        self.assertEqual(before, 0)

    def test_past_due_keeps_plan_but_unpaid_ends_it(self):
        OrgSubscription.objects.create(organization=self.org)
        meta = {"product": "org_plan", "organization_id": str(self.org.pk), "tier": "growth"}
        PaymentService.handle_webhook_event({"type": "customer.subscription.updated", "id": "e3", "data": {"object": {
            "id": "sub_1", "status": "past_due", "metadata": meta, "current_period_end": int((timezone.now() + timedelta(days=5)).timestamp())}}})
        self.assertTrue(org_plans.is_active(OrgSubscription.objects.get(organization=self.org)))
        PaymentService.handle_webhook_event({"type": "customer.subscription.updated", "id": "e4", "data": {"object": {
            "id": "sub_1", "status": "unpaid", "metadata": meta}}})
        self.assertFalse(org_plans.is_active(OrgSubscription.objects.get(organization=self.org)))

    def test_checkout_requires_membership_and_creates_an_inline_priced_subscription(self):
        self.client.force_login(self.outsider)
        with mock.patch("jobs.services.org_plans.create_checkout_session") as create:
            response = self.client.post("/employers/plan/pro/")
        create.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.owner)
        with mock.patch("jobs.services.org_plans.stripe") as stripe_mock:
            stripe_mock.Customer.create.return_value = mock.Mock(id="cus_9")
            stripe_mock.checkout.Session.create.return_value = mock.Mock(
                id="cs_new", expires_at=int((timezone.now() + timedelta(hours=24)).timestamp()), url="https://checkout.stripe.test/s")
            response = self.client.post("/employers/plan/growth/")
        self.assertEqual(response["Location"], "https://checkout.stripe.test/s")
        kwargs = stripe_mock.checkout.Session.create.call_args.kwargs
        self.assertEqual(kwargs["mode"], "subscription")
        item = kwargs["line_items"][0]["price_data"]
        self.assertEqual((item["unit_amount"], item["recurring"]["interval"]), (29900, "month"))
        self.assertEqual(kwargs["subscription_data"]["metadata"]["tier"], "growth")

    def test_pricing_section_and_dashboard_card_render(self):
        page = self.client.get("/employers/")
        self.assertContains(page, "$149")
        self.assertContains(page, "$299")
        self.assertContains(page, "Enterprise")
        from jobs.models import UserProfile
        UserProfile.objects.create(user=self.owner, account_type="employer")
        self.client.force_login(self.owner)
        dash = self.client.get("/account/")
        self.assertContains(dash, "Feature all your roles for $299 a month")  # 8 live roles -> Organization plan

    def pending_subscription(self):
        return OrgSubscription.objects.create(
            organization=self.org, stripe_checkout_session_id="cs_1", stripe_customer_id="cus_1")

    def test_an_active_plan_cannot_start_a_second_subscription_in_another_tier(self):
        OrgSubscription.objects.create(organization=self.org, tier="pro", stripe_subscription_id="sub_current")
        with mock.patch("jobs.services.org_plans.stripe.checkout.Session.create") as create:
            with self.assertRaisesMessage(ValueError, "already has a plan"):
                org_plans.create_checkout_session(self.owner, self.org, "growth", "https://example.org")
        create.assert_not_called()

    def test_expired_local_access_does_not_allow_duplicate_live_stripe_billing(self):
        OrgSubscription.objects.create(organization=self.org, tier="pro", stripe_subscription_id="sub_current",
                                       expires_at=timezone.now() - timedelta(days=1))
        self.retrieve.return_value = {"status": "past_due"}
        with self.assertRaisesMessage(ValueError, "already has a subscription"):
            org_plans.create_checkout_session(self.owner, self.org, "growth", "https://example.org")

    def test_repeated_checkout_reuses_the_open_session(self):
        self.pending_subscription()
        pending = stripe.StripeObject.construct_from({
            "id": "cs_1", "status": "open", "url": "https://checkout.example.org/cs_1",
            "metadata": {"tier": "pro"}}, None)
        with mock.patch("jobs.services.org_plans.stripe.checkout.Session.retrieve", return_value=pending), \
                mock.patch("jobs.services.org_plans.stripe.checkout.Session.create") as create:
            self.assertEqual(org_plans.create_checkout_session(self.owner, self.org, "pro", "https://example.org"), pending.url)
        create.assert_not_called()

    def test_a_different_pending_tier_expires_the_old_checkout(self):
        self.pending_subscription()
        pending = stripe.StripeObject.construct_from({"id": "cs_1", "status": "open", "metadata": {"tier": "pro"}}, None)
        new = mock.Mock(id="cs_new", expires_at=int((timezone.now() + timedelta(hours=24)).timestamp()), url="https://checkout.example.org/new")
        with mock.patch("jobs.services.org_plans.stripe.checkout.Session.retrieve", return_value=pending), \
                mock.patch("jobs.services.org_plans.stripe.checkout.Session.expire") as expire, \
                mock.patch("jobs.services.org_plans.stripe.checkout.Session.create", return_value=new):
            org_plans.create_checkout_session(self.owner, self.org, "growth", "https://example.org")
        expire.assert_called_once_with("cs_1")
        self.assertEqual(OrgSubscription.objects.get(organization=self.org).stripe_checkout_session_id, "cs_new")

    def test_unpaid_checkout_never_features_roles(self):
        sub = self.pending_subscription()
        event = self.session()
        event["data"]["object"]["payment_status"] = "unpaid"
        PaymentService.handle_webhook_event(event)
        self.retrieve.assert_not_called()
        sub.refresh_from_db()
        self.assertEqual(sub.tier, "free")
        self.assertFalse(Job.objects.filter(featured_until__isnull=False).exists())

    def test_checkout_uses_actual_period_end_even_when_previous_plan_has_expired(self):
        sub = self.pending_subscription()
        sub.expires_at = timezone.now() - timedelta(days=3)
        sub.save()
        event = self.session()
        end = self.retrieve.return_value["current_period_end"]
        PaymentService.handle_webhook_event(event)
        sub.refresh_from_db()
        self.assertEqual(int(sub.expires_at.timestamp()), end)

    def test_subscription_created_before_paid_checkout_does_not_activate(self):
        sub = self.pending_subscription()
        self.session()
        org_plans.activate(self.retrieve.return_value)
        sub.refresh_from_db()
        self.assertEqual(sub.tier, "free")

    def test_old_checkout_cannot_replace_the_current_pending_checkout(self):
        sub = self.pending_subscription()
        event = self.session()
        event["data"]["object"]["id"] = "cs_old"
        PaymentService.handle_webhook_event(event)
        self.retrieve.assert_not_called()
        sub.refresh_from_db()
        self.assertEqual(sub.tier, "free")

    def test_checkout_retrieval_failure_remains_retryable(self):
        sub = self.pending_subscription()
        event = self.session()
        self.retrieve.side_effect = RuntimeError("Stripe unavailable")
        with self.assertRaises(RuntimeError):
            PaymentService.handle_webhook_event(event)
        sub.refresh_from_db()
        self.assertEqual(sub.tier, "free")
        self.retrieve.side_effect = None
        PaymentService.handle_webhook_event(event)
        sub.refresh_from_db()
        self.assertEqual(sub.tier, "pro")

    def test_late_checkout_does_not_reactivate_a_cancelled_stripe_subscription(self):
        sub = self.pending_subscription()
        event = self.session()
        self.retrieve.return_value["status"] = "canceled"
        PaymentService.handle_webhook_event(event)
        sub.refresh_from_db()
        self.assertFalse(org_plans.is_active(sub))
        self.assertEqual(sub.stripe_checkout_session_id, "")

    def test_checkout_rejects_a_subscription_for_a_different_customer(self):
        sub = self.pending_subscription()
        event = self.session()
        self.retrieve.return_value["customer"] = "cus_other"
        with self.assertRaises(ValueError):
            PaymentService.handle_webhook_event(event)
        sub.refresh_from_db()
        self.assertEqual(sub.tier, "free")

    def test_old_subscription_updates_and_deletions_do_not_change_current_plan(self):
        sub = OrgSubscription.objects.create(organization=self.org, tier="growth", stripe_subscription_id="sub_current")
        meta = {"product": "org_plan", "organization_id": str(self.org.pk), "tier": "pro"}
        org_plans.activate({"id": "sub_old", "metadata": meta})
        org_plans.deactivate("sub_old", self.org.pk)
        sub.refresh_from_db()
        self.assertEqual((sub.tier, sub.stripe_subscription_id), ("growth", "sub_current"))

    def test_out_of_order_update_does_not_shorten_the_paid_period(self):
        sub = self.pending_subscription()
        event = self.session()
        event["created"] = 200
        PaymentService.handle_webhook_event(event)
        until = OrgSubscription.objects.get(pk=sub.pk).expires_at
        old = dict(self.retrieve.return_value, current_period_end=int((timezone.now() + timedelta(days=1)).timestamp()))
        org_plans.activate(old, event_created=100)
        sub.refresh_from_db()
        self.assertEqual(sub.expires_at, until)

    def test_cancelled_subscription_cannot_be_resurrected_by_a_delayed_update(self):
        sub = self.pending_subscription()
        PaymentService.handle_webhook_event(self.session())
        org_plans.deactivate("sub_1", self.org.pk, event_created=200)
        org_plans.activate(self.retrieve.return_value, event_created=100)
        sub.refresh_from_db()
        self.assertEqual(sub.tier, "free")

    def test_explicit_foreign_or_invalid_organization_never_charges_my_first_org(self):
        self.client.force_login(self.owner)
        other = Organization.objects.create(name="Other", slug="other")
        with mock.patch("jobs.services.org_plans.create_checkout_session") as create:
            for value in (str(other.pk), "invalid"):
                self.assertEqual(self.client.post("/employers/plan/pro/", {"organization": value}).status_code, 302)
        create.assert_not_called()

    def test_plans_do_not_advertise_unimplemented_seats_or_stats_emails(self):
        page = self.client.get("/employers/")
        self.assertNotContains(page, "weekly stats email")
        self.assertNotContains(page, "up to 3 team members")
        self.assertNotContains(page, "up to 10 team members")

    def test_unpaid_and_paused_subscriptions_can_recover_after_payment_or_resumption(self):
        sub = self.pending_subscription()
        PaymentService.handle_webhook_event(self.session())
        for status in ("unpaid", "paused"):
            org_plans.deactivate("sub_1", self.org.pk, status=status)
            org_plans.activate(self.retrieve.return_value)
            sub.refresh_from_db()
            self.assertEqual((sub.tier, sub.stripe_status), ("pro", "active"))

    def test_older_cancellation_does_not_cancel_a_newer_renewal(self):
        sub = self.pending_subscription()
        event = self.session()
        event["created"] = 200
        PaymentService.handle_webhook_event(event)
        org_plans.deactivate("sub_1", self.org.pk, event_created=100)
        sub.refresh_from_db()
        self.assertEqual(sub.tier, "pro")

    def test_checkout_opened_before_release_can_still_complete(self):
        sub = OrgSubscription.objects.create(organization=self.org, stripe_customer_id="cus_1")
        PaymentService.handle_webhook_event(self.session())
        sub.refresh_from_db()
        self.assertEqual((sub.tier, sub.stripe_subscription_id), ("pro", "sub_1"))
