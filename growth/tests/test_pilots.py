from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone
from growth.models import CashMovement, EmployerPilot, PilotActivity
from growth.pilots import eligible_payment, pilot_summary, transition_pilot
from growth.revenue import persist_run
from growth.tests.test_revenue import row
from jobs.models import Organization


class PilotTests(TestCase):
    def setUp(self):
        self.editor = get_user_model().objects.create_superuser("editor", "editor@example.test", "test-password")
        self.org = Organization.objects.create(name="Independent Org", slug="independent-org")
        self.pilot = EmployerPilot.objects.create(organization=self.org, independent_buyer=True)

    def advance(self, target):
        self.pilot = transition_pilot(self.pilot.pk, target, actor=self.editor, note="Reviewed evidence")

    def add_payment(self, live=True, **overrides):
        now = timezone.now()
        movement = row(**overrides)
        persist_run("acct_ri", live, now-timedelta(days=28), now, [movement], True)
        self.pilot.payment = CashMovement.objects.get(external_id=movement["external_id"], livemode=live)
        self.pilot.payment_intent_reference = movement["payment_intent_id"]
        self.pilot.save()

    def quote(self):
        self.advance("qualified")
        self.pilot.scope = "One role listing, approved profile and available-channel distribution. No applicant guarantee."
        self.pilot.terms = "Buyer approval before publication; delivery within agreed window; cancellation/refund agreed in writing."
        self.pilot.quote_minor = 29900
        self.pilot.save()
        self.advance("quoted")

    def test_quote_and_payment_and_buyer_approval_are_real_gates(self):
        self.advance("qualified")
        with self.assertRaises(ValidationError):
            self.advance("quoted")
        self.pilot.scope, self.pilot.terms, self.pilot.quote_minor = "Actual scope", "Actual terms", 29900
        self.pilot.save()
        self.advance("quoted")
        with self.assertRaises(ValidationError):
            self.advance("paid")
        self.add_payment(live=False)
        with self.assertRaises(ValidationError):
            self.advance("paid")
        self.add_payment()
        self.advance("paid")
        with self.assertRaises(ValidationError):
            self.advance("approved")
        self.pilot.buyer_approval = "Written approval reference"
        self.pilot.save()
        self.advance("approved")
        with self.assertRaises(ValidationError):
            self.advance("delivered")
        self.assertEqual(PilotActivity.objects.count(), 4)

    def test_cannot_skip_stages_or_transition_as_nonstaff(self):
        with self.assertRaises(ValidationError):
            self.advance("delivered")
        user = get_user_model().objects.create_user("user")
        with self.assertRaises(PermissionDenied):
            transition_pilot(self.pilot.pk, "qualified", actor=user, note="No")

    def test_refund_invalidates_paid_demand_not_deleted_history(self):
        self.quote()
        self.add_payment()
        self.advance("paid")
        self.assertEqual(pilot_summary()["independent_paid_buyers"], 1)
        now = timezone.now()
        persist_run("acct_ri", True, now-timedelta(days=28), now, [row(
            external_id="txn_refund", source_id="re_one", kind="refund", amount_minor=-29900,
            presentment_amount_minor=-29900, fee_minor=0, net_minor=-29900)], True)
        self.assertFalse(eligible_payment(self.pilot))
        self.assertEqual(pilot_summary()["payment_evidence_gaps"], 1)
        self.assertFalse(pilot_summary()["repeat_demand_gate_passed"])

    def test_empty_tracker_never_claims_validated_demand(self):
        self.assertFalse(pilot_summary()["repeat_demand_gate_passed"])
        self.assertEqual(pilot_summary()["independent_paid_buyers"], 0)

    def test_usd_quote_can_be_paid_with_nok_settlement_without_inventing_fx(self):
        self.quote()
        self.add_payment(currency="nok", amount_minor=310000, fee_minor=10000, net_minor=300000,
                         presentment_currency="usd", presentment_amount_minor=29900)
        self.advance("paid")
        self.assertTrue(eligible_payment(self.pilot))
