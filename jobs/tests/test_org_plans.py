from datetime import timedelta
from unittest import mock

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

    def session(self, tier="pro"):
        return {"type": "checkout.session.completed", "id": "evt_1", "data": {"object": {
            "id": "cs_1", "mode": "subscription", "customer": "cus_1", "subscription": "sub_1",
            "metadata": {"product": "org_plan", "organization_id": str(self.org.pk), "tier": tier}}}}

    def test_checkout_activates_plan_features_newest_roles_up_to_cap_and_notifies(self):
        OrgSubscription.objects.create(organization=self.org)
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
        OrgSubscription.objects.create(organization=self.org)
        long_until = timezone.now() + timedelta(days=25)
        Job.objects.filter(slug="role-7").update(featured_until=long_until)
        PaymentService.handle_webhook_event(self.session("growth"))
        self.assertEqual(Job.objects.filter(featured_until__gt=timezone.now()).count(), 8)
        self.assertEqual(Job.objects.get(slug="role-7").featured_until, long_until)

    def test_cancellation_ends_the_plan_and_stops_featuring(self):
        OrgSubscription.objects.create(organization=self.org)
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
            stripe_mock.checkout.Session.create.return_value = mock.Mock(url="https://checkout.stripe.test/s")
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
