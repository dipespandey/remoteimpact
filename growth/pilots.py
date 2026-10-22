from collections import Counter

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import CashMovement, EmployerPilot, PilotActivity


STEPS = ["inquiry", "qualified", "quoted", "paid", "approved", "delivered", "reported", "closed"]


def eligible_payment(pilot):
    payment = pilot.payment
    if not payment or not payment.livemode or not payment.scope_verified or payment.kind != "charge":
        return False
    if not payment.last_run.complete or not pilot.quote_minor or payment.presentment_currency != pilot.currency:
        return False
    if payment.payment_intent_id != pilot.payment_intent_reference or not pilot.payment_intent_reference:
        return False
    related = CashMovement.objects.filter(
        account_id=payment.account_id, livemode=True, payment_intent_id=payment.payment_intent_id,
        scope_verified=True)
    if related.exclude(presentment_currency=pilot.currency).exists() or related.filter(presentment_amount_minor__isnull=True).exists():
        return False  # An unresolved adjustment cannot silently become zero.
    net_collected = related.aggregate(total=Sum("presentment_amount_minor"))["total"] or 0
    return net_collected >= pilot.quote_minor


@transaction.atomic
def transition_pilot(pilot_id, target, *, actor, note):
    if not actor or not actor.is_active or not actor.is_staff or not actor.has_perm("growth.change_employerpilot"):
        raise PermissionDenied
    pilot = EmployerPilot.objects.select_for_update().get(pk=pilot_id)
    if pilot.payment_id:
        # Lock optional payment evidence separately: PostgreSQL cannot lock
        # the nullable side of the pilot/payment outer join.
        pilot.payment = CashMovement.objects.select_for_update().select_related("last_run").get(pk=pilot.payment_id)
    if not note.strip():
        raise ValidationError("Record evidence for this transition.")
    if target == pilot.stage:
        return pilot
    if target == "canceled":
        if pilot.stage == "canceled":
            return pilot
    elif target not in STEPS or pilot.stage not in STEPS or STEPS.index(target) != STEPS.index(pilot.stage) + 1:
        raise ValidationError("Advance one stage at a time; record missing evidence first.")
    if target == "quoted" and not (pilot.scope.strip() and pilot.terms.strip() and pilot.quote_minor):
        raise ValidationError("A quote needs exact scope, terms and a nonzero amount.")
    if target in ("paid", "approved", "delivered", "reported", "closed") and not eligible_payment(pilot):
        raise ValidationError("A reconciled, live, unrefunded payment covering this quote is required.")
    if target == "approved" and not pilot.buyer_approval.strip():
        raise ValidationError("Record buyer approval before delivery/publication.")
    if target == "delivered" and not pilot.delivery_evidence.strip():
        raise ValidationError("Record actual delivered URLs/channels, not promised exposure.")
    if target == "reported" and not pilot.report_evidence.strip():
        raise ValidationError("Record the report and evidence it was shared.")
    pilot.stage = target
    pilot.save(update_fields=["stage", "updated_at"])
    PilotActivity.objects.create(pilot=pilot, stage=target, actor=actor, note=note)
    return pilot


def pilot_summary():
    counts = Counter()
    paid_orgs = Counter()
    reported = 0
    payment_gaps = 0
    due = 0
    for pilot in EmployerPilot.objects.select_related("payment__last_run"):
        counts[pilot.stage] += 1
        due += bool(pilot.next_action_at and pilot.next_action_at <= timezone.now() and pilot.stage not in ("closed", "canceled"))
        if pilot.stage in STEPS[3:]:
            valid = eligible_payment(pilot)
            payment_gaps += not valid
            if valid and pilot.independent_buyer:
                paid_orgs[pilot.organization_id] += 1
                reported += pilot.stage in ("reported", "closed")
    repeat_buyers = sum(count > 1 for count in paid_orgs.values())
    return {"stages": dict(counts), "independent_paid_buyers": len(paid_orgs),
            "independent_repeat_buyers": repeat_buyers, "reported_pilots": reported,
            "payment_evidence_gaps": payment_gaps, "followups_due": due,
            "repeat_demand_gate_passed": len(paid_orgs) >= 3 and repeat_buyers >= 2 and reported >= 3,
            "caveats": ["No messages are sent by this report.",
                        "Repeat buyers require separate live, reconciled payments; one payment cannot back two pilots.",
                        "Not a forecast, MRR, applicant count or hiring guarantee."]}
