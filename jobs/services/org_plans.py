"""Organization plans: one monthly price that keeps an organization's live roles featured.

Team ($149/mo, up to 5 roles) and Organization ($299/mo, up to 25) are self-serve through Stripe Checkout in
subscription mode, with the price created inline at checkout (no products to set up in the Stripe dashboard).
Enterprise is quoted by email. The webhook activates and ends plans; an hourly sync keeps the newest live roles up
to the plan's cap featured while the plan is active, and featuring simply lapses after the plan ends.
"""
import logging
from datetime import datetime, timedelta, timezone as dt_timezone

import stripe
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

PRODUCT = "org_plan"
FEATURE_WINDOW_DAYS = 2  # kept this far ahead by the sync; featuring lapses within two days of a plan ending
PLANS = {
    "pro": {"name": "Team", "price_cents": 14900, "role_cap": 5, "seats": 3,
            "blurb": "Up to 5 live roles featured, per-role stats and a shared organization page."},
    "growth": {"name": "Organization", "price_cents": 29900, "role_cap": 25, "seats": 10,
               "blurb": "Up to 25 live roles featured, per-role stats and a shared organization page."},
}
ENTERPRISE = {"name": "Enterprise", "from_price_cents": 59900,
              "blurb": "More than 25 roles or several recruiting teams. Quoted by email."}


def plan_for(tier):
    return PLANS.get(tier)


def is_active(subscription) -> bool:
    return bool(subscription and subscription.tier in ("pro", "growth", "enterprise")
                and (subscription.expires_at is None or subscription.expires_at > timezone.now()))


def role_cap(subscription):
    """How many roles the plan features; None means no cap (Enterprise)."""
    plan = PLANS.get(subscription.tier)
    return plan["role_cap"] if plan else None


def recommended_tier(live_roles: int) -> str:
    if live_roles <= PLANS["pro"]["role_cap"]:
        return "pro"
    return "growth" if live_roles <= PLANS["growth"]["role_cap"] else "enterprise"


def individual_cost_cents(roles: int) -> int:
    from jobs.services.payment_service import FEATURE_PRICE_USD_CENTS

    return roles * FEATURE_PRICE_USD_CENTS


# ---------------------------------------------------------------------------
# Checkout and billing portal
# ---------------------------------------------------------------------------

@transaction.atomic
def create_checkout_session(user, organization, tier, domain_url: str):
    from jobs.models import OrgSubscription

    plan = PLANS.get(tier)
    if plan is None:
        raise ValueError("Unknown plan")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    subscription, _ = OrgSubscription.objects.get_or_create(organization=organization)
    subscription = OrgSubscription.objects.select_for_update().get(pk=subscription.pk)
    if is_active(subscription):
        raise ValueError("This organization already has a plan. Manage billing or contact us to change it.")
    if subscription.stripe_subscription_id:
        existing = stripe.Subscription.retrieve(subscription.stripe_subscription_id)
        if existing.get("status") not in ("canceled", "incomplete_expired"):
            raise ValueError("This organization already has a subscription. Manage billing before starting another plan.")
    if subscription.stripe_checkout_session_id:
        pending = stripe.checkout.Session.retrieve(subscription.stripe_checkout_session_id)
        if pending.get("status") == "complete":
            raise ValueError("Your payment is being confirmed. Please refresh your dashboard shortly.")
        if pending.get("status") == "open":
            if (pending.get("metadata") or {}).get("tier") == tier:
                return pending.url
            stripe.checkout.Session.expire(pending.id)
    customer_id = subscription.stripe_customer_id
    if not customer_id:
        customer = stripe.Customer.create(
            email=user.email, name=organization.name,
            metadata={"organization_id": str(organization.pk), "product": PRODUCT},
            idempotency_key=f"org-customer-{organization.pk}",
        )
        customer_id = customer.id
        subscription.stripe_customer_id = customer_id
        subscription.save(update_fields=["stripe_customer_id"])
    metadata = {"product": PRODUCT, "organization_id": str(organization.pk), "tier": tier, "user_id": str(user.pk)}
    session = stripe.checkout.Session.create(
        mode="subscription", customer=customer_id,
        line_items=[{"quantity": 1, "price_data": {
            "currency": "usd", "unit_amount": plan["price_cents"], "recurring": {"interval": "month"},
            "product_data": {"name": f"Remote Impact {plan['name']} plan: {organization.name}"[:250],
                             "description": plan["blurb"]}}}],
        subscription_data={"metadata": metadata}, metadata=metadata, client_reference_id=str(organization.pk),
        success_url=f"{domain_url}{reverse('jobs:account')}?org_plan=success",
        cancel_url=f"{domain_url}{reverse('jobs:employers')}#plans",
        allow_promotion_codes=True,
    )
    subscription.stripe_checkout_session_id = session.id
    subscription.stripe_checkout_expires_at = datetime.fromtimestamp(session.expires_at, tz=dt_timezone.utc)
    subscription.save(update_fields=["stripe_checkout_session_id", "stripe_checkout_expires_at"])
    return session.url


def create_billing_portal_session(organization, domain_url: str):
    from jobs.models import OrgSubscription

    stripe.api_key = settings.STRIPE_SECRET_KEY
    subscription = OrgSubscription.objects.filter(organization=organization).first()
    if not subscription or not subscription.stripe_customer_id:
        raise ValueError("No billing account for this organization yet")
    configurations = stripe.billing_portal.Configuration.list(active=True, limit=1)
    if not configurations.data:
        raise ValueError("Stripe billing portal is not configured")
    session = stripe.billing_portal.Session.create(
        customer=subscription.stripe_customer_id, return_url=f"{domain_url}{reverse('jobs:account')}",
        configuration=configurations.data[0].id)
    return session.url


# ---------------------------------------------------------------------------
# Webhook side
# ---------------------------------------------------------------------------

def is_org_plan_object(data_object) -> bool:
    return (data_object.get("metadata") or {}).get("product") == PRODUCT


def _period_end(data_object):
    end = data_object.get("current_period_end")
    if not end:
        periods = [i.get("current_period_end") for i in ((data_object.get("items") or {}).get("data") or []) if i.get("current_period_end")]
        end = max(periods, default=None)
    return datetime.fromtimestamp(end, tz=dt_timezone.utc) if end else None


@transaction.atomic
def activate(data_object, *, event_created=0, checkout=False):
    """Apply a subscription update, or a verified checkout's current Stripe subscription."""
    from jobs.models import EmployerFunnelEvent, OrgSubscription
    from jobs.services.funnel import record_employer_event

    metadata = data_object.get("metadata") or {}
    tier = metadata.get("tier")
    if tier not in PLANS:
        logger.warning("Org plan event with unknown tier %s", tier)
        return None
    subscription = OrgSubscription.objects.select_for_update().filter(organization_id=metadata.get("organization_id")).first()
    if subscription is None:
        logger.warning("Org plan event for unknown organization %s", metadata.get("organization_id"))
        return None
    subscription_id = data_object.get("subscription") or data_object.get("id") or ""
    if not subscription_id:
        return None
    if not checkout:
        if subscription.stripe_subscription_id and subscription.stripe_subscription_id != subscription_id:
            return None
        if subscription.stripe_checkout_session_id and not subscription.stripe_subscription_id:
            return None
        if subscription.stripe_subscription_id == subscription_id and subscription.stripe_status in ("canceled", "incomplete_expired"):
            return None
    if event_created and event_created < subscription.stripe_event_created:
        return None
    was_active = is_active(subscription)
    subscription.tier = tier
    subscription.stripe_subscription_id = subscription_id
    subscription.stripe_customer_id = data_object.get("customer") or subscription.stripe_customer_id
    subscription.expires_at = _period_end(data_object) or (timezone.now() + timedelta(days=32))
    subscription.stripe_event_created = max(event_created, subscription.stripe_event_created)
    subscription.stripe_status = data_object.get("status") or "active"
    if checkout:
        subscription.stripe_checkout_session_id = ""
        subscription.stripe_checkout_expires_at = None
    subscription.save()
    if not was_active:
        record_employer_event(EmployerFunnelEvent.Event.PAYMENT_COMPLETED, metadata={
            "product": PRODUCT, "tier": tier, "organization": subscription.organization.name,
            "amount_cents": PLANS[tier]["price_cents"]})
    sync_featuring(subscription.organization)
    return subscription


@transaction.atomic
def deactivate(subscription_id: str, organization_id=None, *, event_created=0, status="canceled"):
    from jobs.models import OrgSubscription

    qs = OrgSubscription.objects.select_for_update().filter(stripe_subscription_id=subscription_id) if subscription_id else OrgSubscription.objects.none()
    if organization_id:
        qs = qs.filter(organization_id=organization_id)
    subscription = qs.first()
    if subscription is None:
        return None
    if event_created and event_created < subscription.stripe_event_created:
        return None
    subscription.tier = OrgSubscription.Tier.FREE
    subscription.expires_at = timezone.now()
    subscription.stripe_checkout_session_id = ""
    subscription.stripe_checkout_expires_at = None
    subscription.stripe_event_created = max(event_created, subscription.stripe_event_created)
    subscription.stripe_status = status
    subscription.save(update_fields=["tier", "expires_at", "stripe_event_created", "stripe_checkout_session_id", "stripe_checkout_expires_at", "stripe_status"])
    return subscription


@transaction.atomic
def complete_checkout(session, *, event_created=0):
    """Only the pending checkout may replace a plan; read Stripe's current billing state."""
    from jobs.models import OrgSubscription

    if session.get("payment_status") not in ("paid", "no_payment_required"):
        return None
    metadata = session.get("metadata") or {}
    subscription = OrgSubscription.objects.select_for_update().filter(
        organization_id=metadata.get("organization_id")).first()
    if subscription is None:
        return None
    if subscription.stripe_checkout_session_id != session.get("id"):
        # Checkouts opened before this release have no stored session id. Accept
        # those only for an organization that has never activated a subscription;
        # the paid event, customer and current Stripe metadata are still verified.
        legacy_pending = not subscription.stripe_checkout_session_id and not subscription.stripe_subscription_id and not is_active(subscription)
        if not legacy_pending:
            return None
    stripe.api_key = settings.STRIPE_SECRET_KEY
    current = stripe.Subscription.retrieve(session.get("subscription"))
    current_metadata = current.get("metadata") or {}
    if current.get("customer") != subscription.stripe_customer_id or any(
            current_metadata.get(key) != value for key, value in metadata.items()):
        raise ValueError("Checkout subscription does not match its organization and plan")
    if current.get("status") in ("active", "trialing", "past_due"):
        return activate(current, event_created=event_created, checkout=True)
    if not subscription.stripe_subscription_id or subscription.stripe_subscription_id == current.get("id"):
        subscription.stripe_subscription_id = current.get("id")
        subscription.save(update_fields=["stripe_subscription_id"])
        return deactivate(current.get("id"), metadata.get("organization_id"), event_created=event_created,
                          status=current.get("status") or "canceled")
    return None


# ---------------------------------------------------------------------------
# Featuring
# ---------------------------------------------------------------------------

def sync_featuring(organization=None) -> int:
    """Keep the newest live roles, up to each plan's cap, featured. Returns how many roles were touched."""
    from jobs.models import Job, OrgSubscription
    from jobs.services.job_service import JobService

    now = timezone.now()
    until = now + timedelta(days=FEATURE_WINDOW_DAYS)
    subscriptions = OrgSubscription.objects.exclude(tier=OrgSubscription.Tier.FREE).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    if organization is not None:
        subscriptions = subscriptions.filter(organization=organization)
    touched = 0
    for subscription in subscriptions.select_related("organization"):
        live = JobService.get_available_jobs().filter(organization=subscription.organization).order_by("-posted_at", "-id")
        cap = role_cap(subscription)
        ids = list(live.values_list("pk", flat=True)[:cap] if cap else live.values_list("pk", flat=True))
        # Only ever extend: a role someone paid to feature for 30 days keeps its longer window.
        touched += Job.objects.filter(pk__in=ids).filter(Q(featured_until__isnull=True) | Q(featured_until__lt=until)).update(featured_until=until)
    return touched
