from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock, skipUnless

import stripe
from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import RequestFactory, TransactionTestCase
from django.utils import timezone

from jobs.api import auth, usage
from jobs.models import ApiKey, ApiUsage, Organization, OrgSubscription
from jobs.services import org_plans


@skipUnless(connection.vendor == "postgresql", "Row-lock concurrency requires PostgreSQL")
class BillingApiConcurrencyTests(TransactionTestCase):
    def parallel(self, function, count=12):
        def run(_):
            try:
                return function()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(run, range(count)))

    def test_parallel_checkout_creates_only_one_billable_session(self):
        owner = get_user_model().objects.create_user("owner", email="owner@example.org")
        org = Organization.objects.create(name="Test Org", slug="test-org")
        OrgSubscription.objects.create(organization=org, stripe_customer_id="cus_1")
        session = stripe.StripeObject.construct_from({
            "id": "cs_1", "status": "open", "metadata": {"tier": "pro"},
            "expires_at": int((timezone.now() + timedelta(hours=24)).timestamp()),
            "url": "https://checkout.example.org/cs_1"}, None)
        with mock.patch("jobs.services.org_plans.stripe.checkout.Session.create", return_value=session) as create, \
                mock.patch("jobs.services.org_plans.stripe.checkout.Session.retrieve", return_value=session):
            results = self.parallel(lambda: org_plans.create_checkout_session(owner, org, "pro", "https://example.org"))
        self.assertEqual(set(results), {session.url})
        create.assert_called_once()

    def test_parallel_key_requests_admit_exactly_the_daily_allowance(self):
        _, key = ApiKey.issue(name="Test", email="test@example.org")

        def request():
            caller = auth.Caller(tier="free", key=ApiKey.objects.get(pk=key.pk), scope=f"key:{key.pk}")
            try:
                auth._count(caller)
                return True
            except auth.RateLimited:
                return False

        with mock.patch.dict(auth.TIERS["free"], per_day=3):
            self.assertEqual(sum(self.parallel(request)), 3)
        key.refresh_from_db()
        self.assertEqual(key.requests_today, 3)

    def test_parallel_counter_increments_are_not_lost(self):
        self.parallel(lambda: auth._bump("concurrency-test", 120))
        from jobs.models import ApiRateBucket

        self.assertEqual(ApiRateBucket.objects.get().count, 12)

    def test_parallel_anonymous_requests_admit_exactly_the_minute_allowance(self):
        def request():
            try:
                auth._count(auth.Caller(tier="anonymous", scope="ip:parallel-fixture"))
                return True
            except auth.RateLimited:
                return False
        with mock.patch.dict(auth.TIERS["anonymous"], per_minute=3):
            self.assertEqual(sum(self.parallel(request)), 3)

    def test_parallel_first_usage_events_are_all_recorded(self):
        request = RequestFactory().get("/api/v1/areas/", HTTP_USER_AGENT="concurrency-test")
        caller = auth.Caller(tier="anonymous")
        self.parallel(lambda: usage.record(request, caller, "rest", "areas"))
        self.assertEqual(ApiUsage.objects.get().count, 12)
