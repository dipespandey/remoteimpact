"""Read-only Stripe reconciliation. Never infer money from entitlements or events."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.db import transaction
import stripe

from jobs.models import AssistantSubscription, Job, JobFeaturePurchase, OrgSubscription
from .models import CashMovement, EmployerPilot, OperatingCost, RevenueRun


def object_id(value):
    return value.get("id", "") if isinstance(value, dict) else value or ""


def bounded_list(service, params, limit):
    """Return partial evidence explicitly, never silently count a capped page as all."""
    rows = []
    while len(rows) < limit:
        page = service.list({**params, "limit": min(100, limit - len(rows)),
                             **({"starting_after": rows[-1]["id"]} if rows else {})})
        rows.extend(page["data"])
        if not page.get("has_more"):
            return rows, True
        if not page["data"]:
            return rows, False
    return rows, False


def known_references(client, limit=1000, *, livemode=True):
    """Correlate only locally recorded checkout/payment/subscription identifiers.

    Customer email or a generic metadata.product name is not proof of ownership.
    Missing legacy references mean incomplete scope, not zero RemoteImpact sales.
    """
    intents = {v: "job_posting" for v in Job.objects.exclude(stripe_payment_intent="").values_list("stripe_payment_intent", flat=True)}
    intents.update({v: "employer_pilot" for v in EmployerPilot.objects.exclude(payment_intent_reference="").values_list("payment_intent_reference", flat=True)})
    sessions = {}
    subscriptions = {}
    for model, product in ((Job, "job_posting"), (JobFeaturePurchase, "feature"),
                           (AssistantSubscription, "pro"), (OrgSubscription, "employer_plan")):
        sessions.update({v: product for v in model.objects.exclude(stripe_checkout_session_id="").values_list("stripe_checkout_session_id", flat=True) if v})
        if model in (AssistantSubscription, OrgSubscription):
            subscriptions.update({v: product for v in model.objects.exclude(stripe_subscription_id="").values_list("stripe_subscription_id", flat=True) if v})
    if len(sessions) + len(subscriptions) > limit:
        raise ValidationError("Payment reference limit reached; narrow/review before reconciliation.")
    missing = 0
    def retrieve_session(sid):
        nonlocal missing
        if sid.startswith("cs_test_" if livemode else "cs_live_"):
            missing += 1
            return None
        try:
            return client.v1.checkout.sessions.retrieve(sid)
        except stripe.InvalidRequestError as exc:
            if exc.code != "resource_missing":
                raise
            missing += 1
            return None
    for sid, product in sessions.items():
        session = retrieve_session(sid)
        if session is None:
            continue
        if session.get("payment_intent"):
            intents[object_id(session["payment_intent"])] = product
        if session.get("subscription"):
            subscriptions[object_id(session["subscription"])] = product
    # Capture recurring payments, including post-trial charges, without counting
    # the subscription itself as cash. Supports current and legacy invoice shapes.
    remaining = limit
    for sid, product in subscriptions.items():
        try:
            invoices, complete = bounded_list(client.v1.invoices, {"subscription": sid}, remaining)
        except stripe.InvalidRequestError as exc:
            if exc.code != "resource_missing":
                raise
            missing += 1
            continue
        remaining -= len(invoices)
        if not complete:
            raise ValidationError("Invoice pagination incomplete; no financial snapshot saved.")
        for invoice in invoices:
            pi = object_id(invoice.get("payment_intent"))
            if pi:
                intents[pi] = product
            else:
                payments, complete = bounded_list(client.v1.invoice_payments, {"invoice": invoice["id"]}, max(1, remaining))
                remaining -= len(payments)
                if not complete or remaining < 0:
                    raise ValidationError("Invoice payment pagination incomplete.")
                for payment in payments:
                    pi = object_id((payment.get("payment") or {}).get("payment_intent"))
                    if pi:
                        intents[pi] = product
        if remaining <= 0:
            raise ValidationError("Invoice reference budget exhausted.")
    return intents, {"unresolved_or_other_mode_local_references": missing,
                     "note": "Unresolved references are not silently converted into payments or zero sales."}


def movement_row(item, client, references, charge_cache):
    source = item.get("source")
    sid = object_id(source)
    charge = None
    original_source = None
    # Expanded sources are not persisted: they can contain private customer data.
    if sid.startswith("ch_"):
        if sid not in charge_cache:
            charge_cache[sid] = source if isinstance(source, dict) else client.v1.charges.retrieve(sid)
        charge = charge_cache[sid]
        if (item.get("reporting_category") or item["type"]) == "charge":
            original_source = charge
    elif sid.startswith(("re_", "dp_")):
        obj = source if isinstance(source, dict) else (
            client.v1.refunds.retrieve(sid) if sid.startswith("re_") else client.v1.disputes.retrieve(sid))
        original_source = obj
        cid = object_id(obj.get("charge"))
        if cid:
            if cid not in charge_cache:
                charge_cache[cid] = client.v1.charges.retrieve(cid)
            charge = charge_cache[cid]
    pi = object_id((charge or {}).get("payment_intent"))
    product = references.get(pi, "")
    timestamp = lambda value: datetime.fromtimestamp(value, dt_timezone.utc) if value else None
    amount, fee, net = (item[key] for key in ("amount", "fee", "net"))
    if any(type(v) is not int for v in (amount, fee, net)) or net != amount - fee:
        raise ValidationError("Processor amount/fee/net did not reconcile.")
    original_currency = (original_source or {}).get("currency", "")
    original_amount = (original_source or {}).get("amount_captured", (original_source or {}).get("amount"))
    if type(original_amount) is not int or not original_currency:
        original_amount, original_currency = None, ""
    else:
        original_amount = abs(original_amount) * (-1 if amount < 0 else 1)
    return {"external_id": item["id"], "source_id": sid, "payment_intent_id": pi,
            "kind": item.get("reporting_category") or item["type"], "currency": item["currency"].lower(),
            "amount_minor": amount, "fee_minor": fee, "net_minor": net,
            "presentment_currency": original_currency, "presentment_amount_minor": original_amount,
            "occurred_at": timestamp(item["created"]), "available_at": timestamp(item.get("available_on")),
            "scope_verified": bool(product), "scope_basis": "local_processor_reference" if product else "unresolved",
            "product": product}


def summarize(rows):
    currencies = defaultdict(lambda: {"receipts_minor": 0, "refunds_minor": 0, "other_adjustments_minor": 0,
                                      "processor_fees_minor": 0, "balance_net_minor": 0, "unresolved_rows": 0})
    by_product = defaultdict(lambda: defaultdict(int))
    presentment = defaultdict(lambda: {"receipts_minor": 0, "refunds_minor": 0, "other_adjustments_minor": 0})
    unresolved = defaultdict(dict)
    for row in rows:
        current = currencies[row["currency"]]
        if not row["scope_verified"]:
            current["unresolved_rows"] += 1
            group = unresolved[row["currency"]].setdefault(row["kind"], {
                "rows": 0, "amount_minor": 0, "fee_minor": 0, "net_minor": 0})
            group["rows"] += 1
            for key in ("amount_minor", "fee_minor", "net_minor"):
                group[key] += row[key]
            continue
        amount = row["amount_minor"]
        key = "receipts_minor" if row["kind"] == "charge" else "refunds_minor" if row["kind"] == "refund" else "other_adjustments_minor"
        current[key] += amount  # Refunds/chargebacks retain their negative sign.
        if row.get("presentment_currency") and row.get("presentment_amount_minor") is not None:
            presentment[row["presentment_currency"]][key] += row["presentment_amount_minor"]
        current["processor_fees_minor"] += row["fee_minor"]
        current["balance_net_minor"] += row["net_minor"]
        by_product[row["product"]][row["currency"]] += row["net_minor"]
    return {"currencies": dict(currencies), "customer_payment_currencies": dict(presentment),
            "unresolved_balance_activity": dict(unresolved),
            "by_product_net_minor": {k: dict(v) for k, v in by_product.items()},
            "rows": len(rows), "scoped_rows": sum(row["scope_verified"] for row in rows),
            "scope_complete": all(row["scope_verified"] for row in rows),
            "caveats": ["Amounts are integer processor minor units, grouped by settlement currency; no FX conversion.",
                        "This is processor balance activity by creation time, not bank payouts, MRR or accounting revenue.",
                        "Unresolved rows include payouts, unrelated activity and legacy payments without local references.",
                        "Scoped totals may be partial. Empty scoped totals do not prove zero sales.",
                        "Unresolved account-level fees/adjustments are shown separately; they are not assumed to be zero or allocated to project profit.",
                        "Tax, non-Stripe expenses and owner labor are not deducted here."]}


@transaction.atomic
def persist_run(account_id, livemode, since, until, rows, complete, reference_coverage=None):
    summary = summarize(rows)
    if reference_coverage is not None:
        summary["reference_coverage"] = reference_coverage
    run = RevenueRun.objects.create(account_id=account_id, livemode=livemode, since=since, until=until,
                                    complete=complete, summary=summary)
    for row in rows:
        identity = {"account_id": account_id, "livemode": livemode, "external_id": row["external_id"]}
        existing = CashMovement.objects.filter(**identity).first()
        if existing and any(getattr(existing, key) != row[key] for key in ("currency", "amount_minor", "fee_minor", "net_minor", "occurred_at", "source_id", "presentment_currency", "presentment_amount_minor")):
            raise ValidationError("Existing processor movement changed; investigate rather than overwrite money.")
        CashMovement.objects.update_or_create(**identity, defaults={**row, "last_run": run})
    return run


def cost_summary(since, until):
    totals = defaultdict(lambda: {"recorded_cash_cost_minor": 0, "recorded_owner_labor_minor": 0})
    last_date = until.date() if until.time().isoformat() != "00:00:00" else until.date() - timedelta(days=1)
    for cost in OperatingCost.objects.filter(date__gte=since.date(), date__lte=last_date):
        key = "recorded_owner_labor_minor" if cost.is_owner_labor else "recorded_cash_cost_minor"
        totals[cost.currency][key] += cost.amount_minor
    return {"currencies": dict(totals), "coverage": "owner verification required",
            "profit": None, "note": "Costs are recorded by calendar date, not intraday timestamp. No rows does not mean zero costs. Tax/cost completeness is not established."}
