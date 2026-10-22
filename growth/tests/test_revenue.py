from datetime import timedelta
from io import StringIO
import json
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from growth.models import CashMovement, RevenueRun
from growth.revenue import bounded_list, movement_row, persist_run, summarize


def row(**overrides):
    now = timezone.now().replace(microsecond=0)
    values = dict(external_id="txn_one", source_id="ch_one", payment_intent_id="pi_one", kind="charge",
                  currency="usd", amount_minor=29900, fee_minor=900, net_minor=29000,
                  presentment_currency="usd", presentment_amount_minor=29900,
                  occurred_at=now, available_at=now, scope_verified=True,
                  scope_basis="local_processor_reference", product="employer_pilot")
    result = {**values, **overrides}
    if "presentment_amount_minor" not in overrides:
        result["presentment_amount_minor"] = result["amount_minor"]
    if "presentment_currency" not in overrides:
        result["presentment_currency"] = result["currency"]
    return result


class RevenueArithmeticTests(SimpleTestCase):
    def test_refunds_fees_disputes_and_currencies_are_not_sales_or_fx(self):
        report = summarize([row(), row(kind="refund", amount_minor=-5000, fee_minor=0, net_minor=-5000),
            row(kind="dispute", amount_minor=-1000, fee_minor=100, net_minor=-1100),
            row(currency="eur", amount_minor=1000, fee_minor=50, net_minor=950),
            row(scope_verified=False, kind="payout", amount_minor=-20000, net_minor=-20000, fee_minor=0)])
        self.assertEqual(report["currencies"]["usd"], {
            "receipts_minor": 29900, "refunds_minor": -5000, "other_adjustments_minor": -1000,
            "processor_fees_minor": 1000, "balance_net_minor": 22900, "unresolved_rows": 1})
        self.assertEqual(report["currencies"]["eur"]["balance_net_minor"], 950)
        self.assertFalse(report["scope_complete"])
        self.assertEqual(report["unresolved_balance_activity"]["usd"]["payout"]["net_minor"], -20000)

    def test_pagination_reports_cap_and_fetches_all_pages(self):
        service = Mock()
        service.list.side_effect = [{"data": [{"id": "a"}], "has_more": True},
                                    {"data": [{"id": "b"}], "has_more": False}]
        items, complete = bounded_list(service, {}, 10)
        self.assertTrue(complete)
        self.assertEqual(len(items), 2)
        self.assertEqual(service.list.call_args.args[0]["starting_after"], "a")
        service.list.side_effect = [{"data": [{"id": "a"}], "has_more": True}]
        self.assertFalse(bounded_list(service, {}, 1)[1])

    def test_charge_scope_uses_local_pi_not_email_or_generic_metadata(self):
        item = dict(id="txn_x", type="charge", reporting_category="charge", currency="usd",
                    amount=1000, fee=50, net=950, created=100000, available_on=110000,
                    source={"id": "ch_one", "payment_intent": "pi_unknown", "metadata": {"product": "pro"}, "receipt_email": "private@example.org"})
        result = movement_row(item, Mock(), {}, {})
        self.assertFalse(result["scope_verified"])
        self.assertNotIn("private", json.dumps(result, default=str))
        self.assertTrue(movement_row(item, Mock(), {"pi_unknown": "pro"}, {})["scope_verified"])

    def test_old_charge_refund_is_scoped_in_refund_period(self):
        client = Mock()
        client.v1.charges.retrieve.return_value = {"id": "ch_old", "payment_intent": "pi_old"}
        item = dict(id="txn_refund", type="refund", currency="usd", amount=-1000, fee=0, net=-1000,
                    created=100000, source={"id": "re_one", "charge": "ch_old"})
        result = movement_row(item, client, {"pi_old": "job_posting"}, {})
        self.assertTrue(result["scope_verified"])
        self.assertEqual(result["amount_minor"], -1000)

    def test_customer_currency_and_settlement_currency_stay_separate(self):
        item = dict(id="txn_fx", type="charge", currency="nok", amount=310000, fee=10000, net=300000,
                    created=100000, source={"id": "ch_usd", "payment_intent": "pi_usd",
                                            "currency": "usd", "amount": 29900, "amount_captured": 29900})
        result = movement_row(item, Mock(), {"pi_usd": "employer_pilot"}, {})
        self.assertEqual((result["currency"], result["amount_minor"]), ("nok", 310000))
        self.assertEqual((result["presentment_currency"], result["presentment_amount_minor"]), ("usd", 29900))
        report = summarize([result])
        self.assertEqual(report["customer_payment_currencies"]["usd"]["receipts_minor"], 29900)
        self.assertEqual(report["currencies"]["nok"]["balance_net_minor"], 300000)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("growth.management.commands.reconcile_revenue.stripe.StripeClient")
    def test_wrong_mode_fails_before_api(self, factory):
        with self.assertRaises(CommandError):
            call_command("reconcile_revenue", expected_account="acct_remoteimpact", mode="live")
        factory.assert_not_called()

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("growth.management.commands.reconcile_revenue.stripe.StripeClient")
    def test_wrong_account_fails_before_transaction_access(self, factory):
        factory.return_value.v1.accounts.retrieve_current.return_value = {"id": "acct_other"}
        with self.assertRaises(CommandError):
            call_command("reconcile_revenue", expected_account="acct_remoteimpact", mode="test")
        factory.return_value.v1.balance_transactions.list.assert_not_called()


class RevenuePersistenceTests(TestCase):
    def test_duplicate_runs_do_not_duplicate_cash_or_mix_modes(self):
        now = timezone.now()
        movement = row()
        for _ in range(2):
            persist_run("acct_ri", True, now-timedelta(days=28), now, [movement], True)
        self.assertEqual(CashMovement.objects.count(), 1)
        persist_run("acct_ri", False, now-timedelta(days=28), now, [movement], True)
        self.assertEqual(CashMovement.objects.count(), 2)

    def test_changed_money_aborts_whole_run(self):
        now = timezone.now()
        original = row()
        persist_run("acct_ri", True, now-timedelta(days=28), now, [original], True)
        with self.assertRaises(ValidationError):
            persist_run("acct_ri", True, now-timedelta(days=28), now,
                        [{**original, "amount_minor": 1}], True)
        self.assertEqual(RevenueRun.objects.count(), 1)
        self.assertEqual(CashMovement.objects.get().amount_minor, 29900)

    def test_missing_finance_is_unknown_not_zero(self):
        output = StringIO()
        call_command("growth_report", account="acct_ri", stdout=output)
        result = json.loads(output.getvalue())
        self.assertIsNone(result["cash"])
        self.assertIsNone(result["owner_profit"])
