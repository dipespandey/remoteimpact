import logging
from datetime import datetime, timedelta, timezone as dt_timezone

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from ..models import Job

User = get_user_model()
logger = logging.getLogger(__name__)


JOB_POSTING_PRICE_USD_CENTS = 9900  # $99.00, includes featured placement
JOB_POSTING_DURATION_DAYS = 30
FEATURE_PRICE_USD_CENTS = 4900  # $49.00 to feature an existing listing
FEATURE_DURATION_DAYS = 30

PRO_TRIAL_DAYS = 7
PRO_PLANS = {
    "monthly": {
        "label": "Pro monthly",
        "unit_amount": 1000,
        "interval": "month",
        "description": "AI Job Scouts plus unlimited application tools",
    },
    "pass": {
        "label": "3-month Job Search Pass",
        "unit_amount": 2400,
        "days": 90,
        "description": "Three months of AI Job Scouts and application tools. One payment, no renewal.",
    },
}
DEFAULT_PRO_PLAN = "monthly"


def normalize_pro_plan(plan):
    return plan if plan in PRO_PLANS else DEFAULT_PRO_PLAN


def pro_trial_eligible(subscription) -> bool:
    """A free trial is for people who have never had Pro before."""
    return not subscription.subscribed_at and not subscription.stripe_subscription_id


class PaymentService:
    @staticmethod
    def create_checkout_session(job: Job, domain_url: str):
        """Create a Stripe Checkout Session for a job posting and return its URL.

        Persists the session id on the Job so the webhook can correlate the
        completion event back even if the user never returns from Stripe.
        """
        stripe.api_key = settings.STRIPE_SECRET_KEY

        checkout_session = stripe.checkout.Session.create(
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": JOB_POSTING_PRICE_USD_CENTS,
                        "product_data": {
                            "name": f"Job Posting: {job.title}",
                            "description": f"{JOB_POSTING_DURATION_DAYS}-day job listing on Remote Impact",
                        },
                    },
                    "quantity": 1,
                },
            ],
            mode="payment",
            invoice_creation={"enabled": True},
            success_url=(
                f"{domain_url}{reverse('jobs:payment_success')}"
                f"?session_id={{CHECKOUT_SESSION_ID}}&job_id={job.id}"
            ),
            cancel_url=(
                f"{domain_url}{reverse('jobs:payment_cancel')}?job_id={job.id}"
            ),
            metadata={"job_id": str(job.id)},
            client_reference_id=str(job.id),
            idempotency_key=f"job-checkout-{job.id}",
        )

        Job.objects.filter(pk=job.pk).update(
            stripe_checkout_session_id=checkout_session.id
        )
        return checkout_session.url

    @staticmethod
    def verify_payment(session_id: str, job_id: str):
        """Verify a Checkout Session and activate the job if paid.

        Returns (success: bool, job: Job | None). Validates that the session's
        metadata.job_id matches the Job we're about to activate so a paid
        session can't be reused to activate someone else's draft.
        """
        stripe.api_key = settings.STRIPE_SECRET_KEY

        try:
            session = stripe.checkout.Session.retrieve(session_id)
        except stripe.error.StripeError:
            logger.exception("Stripe session retrieve failed: %s", session_id)
            return False, None

        if session.payment_status != "paid":
            return False, None

        metadata_job_id = (session.metadata or {}).get("job_id")
        if str(metadata_job_id) != str(job_id):
            logger.warning(
                "Stripe session %s metadata.job_id=%s does not match url job_id=%s",
                session_id,
                metadata_job_id,
                job_id,
            )
            return False, None

        try:
            job = Job.objects.get(id=job_id)
        except (Job.DoesNotExist, ValueError, TypeError):
            return False, None

        PaymentService._mark_job_paid(
            job,
            session_id=session.id,
            payment_intent=session.payment_intent or "",
        )
        return True, job

    @staticmethod
    def handle_webhook_event(event):
        """Handle a verified Stripe event. Idempotent: safe to call repeatedly.

        Only ``checkout.session.completed`` and ``checkout.session.async_payment_succeeded``
        cause activation. Failures and expirations are logged but otherwise no-op
        because the Job remains inactive by default.
        """
        event_type = event.get("type")
        event_id = event.get("id") or ""
        data_object = event.get("data", {}).get("object", {}) or {}

        if event_type in (
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        ):
            if data_object.get("mode") == "subscription":
                PaymentService._activate_assistant_from_stripe(data_object)
                return
            if data_object.get("payment_status") != "paid":
                return
            if (data_object.get("metadata") or {}).get("plan") == "pass":
                PaymentService._activate_pass_from_stripe(data_object)
                return
            if (data_object.get("metadata") or {}).get("product") == "feature":
                PaymentService._activate_feature(data_object)
                return

            metadata = data_object.get("metadata") or {}
            job_id = metadata.get("job_id") or data_object.get("client_reference_id")
            if not job_id:
                logger.warning(
                    "Stripe webhook %s missing job_id metadata: %s",
                    event_type,
                    data_object.get("id"),
                )
                return

            try:
                job = Job.objects.get(id=job_id)
            except Job.DoesNotExist:
                logger.warning(
                    "Stripe webhook %s references unknown job_id=%s", event_type, job_id
                )
                return

            PaymentService._mark_job_paid(
                job,
                session_id=data_object.get("id") or "",
                payment_intent=data_object.get("payment_intent") or "",
            )
            return

        if event_type in (
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.resumed",
        ):
            if data_object.get("status") in ("active", "trialing"):
                PaymentService._activate_assistant_from_stripe(data_object)
            return

        if event_type == "customer.subscription.trial_will_end":
            PaymentService._send_trial_ending_reminder(data_object, event_id)
            return

        if event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
            from ..models import AssistantSubscription, ConsumerFunnelEvent
            from .funnel import record_consumer_event

            subscription_id = data_object.get("id")
            subscription = AssistantSubscription.objects.filter(
                stripe_subscription_id=subscription_id
            ).select_related("user").first()
            if subscription:
                subscription.is_subscribed = False
                subscription.expires_at = timezone.now()
                subscription.save(update_fields=["is_subscribed", "expires_at"])
                record_consumer_event(
                    ConsumerFunnelEvent.Event.SUBSCRIPTION_CANCELLED,
                    user=subscription.user,
                    metadata={
                        "stripe_subscription_id": subscription_id,
                        "stripe_event_type": event_type,
                    },
                    dedupe_key=(
                        f"stripe-event:{event_id}:subscription-cancelled"
                        if event_id
                        else f"subscription-cancelled:{subscription_id}"
                    ),
                )
            return

        if event_type == "checkout.session.expired":
            from ..models import AssistantSubscription, ConsumerFunnelEvent
            from .funnel import record_consumer_event

            metadata = data_object.get("metadata") or {}
            session_id = data_object.get("id") or ""
            subscription = AssistantSubscription.objects.filter(
                user_id=metadata.get("user_id")
            ).select_related("user").first()
            if subscription:
                record_consumer_event(
                    ConsumerFunnelEvent.Event.CHECKOUT_EXPIRED,
                    user=subscription.user,
                    metadata={
                        "checkout_session_id": session_id,
                        **({"source": metadata["source"]} if metadata.get("source") else {}),
                    },
                    dedupe_key=f"stripe-event:{event_id}:checkout-expired",
                )
                if subscription.stripe_checkout_session_id == session_id:
                    AssistantSubscription.objects.filter(pk=subscription.pk).update(
                        stripe_checkout_session_id="",
                        stripe_checkout_expires_at=None,
                    )
            logger.info("Stripe checkout expired for session %s", session_id)
            return

        if event_type == "checkout.session.async_payment_failed":
            from ..models import AssistantSubscription, ConsumerFunnelEvent
            from .funnel import record_consumer_event

            metadata = data_object.get("metadata") or {}
            subscription = AssistantSubscription.objects.filter(
                user_id=metadata.get("user_id")
            ).select_related("user").first()
            if subscription:
                record_consumer_event(
                    ConsumerFunnelEvent.Event.CHECKOUT_FAILED,
                    user=subscription.user,
                    metadata={
                        "checkout_session_id": data_object.get("id") or "",
                        "failure_stage": "async_payment",
                    },
                    dedupe_key=f"stripe-event:{event_id}:checkout-failed",
                )
            logger.info(
                "Stripe checkout %s for session %s",
                event_type,
                data_object.get("id"),
            )
            return

    @staticmethod
    def _send_trial_ending_reminder(data_object, event_id=""):
        from ..models import AssistantSubscription

        subscription = AssistantSubscription.objects.filter(
            stripe_subscription_id=data_object.get("id")
        ).select_related("user").first()
        if not subscription:
            user_id = (data_object.get("metadata") or {}).get("user_id")
            subscription = AssistantSubscription.objects.filter(user_id=user_id).select_related("user").first()
        if not subscription:
            return False
        trial_end = data_object.get("trial_end")
        trial_end = datetime.fromtimestamp(trial_end, tz=dt_timezone.utc) if trial_end else subscription.expires_at
        return PaymentService.send_trial_reminder(
            subscription, trial_end, stripe_subscription_id=data_object.get("id") or "", event_id=event_id
        )

    @staticmethod
    def send_trial_reminder(subscription, trial_end, *, stripe_subscription_id="", event_id=""):
        """Email a trialing subscriber once before the first charge."""
        from ..models import ConsumerFunnelEvent, JobAlertDelivery
        from .email_service import email_service

        dedupe_key = f"trial-reminder:{stripe_subscription_id or subscription.stripe_subscription_id}"
        if ConsumerFunnelEvent.objects.filter(dedupe_key=dedupe_key).exists():
            return False
        match_count = JobAlertDelivery.objects.filter(alert__user=subscription.user).count()
        if not email_service.send_pro_trial_ending(subscription.user, trial_end, match_count):
            return False
        ConsumerFunnelEvent.objects.create(
            event=ConsumerFunnelEvent.Event.TRIAL_REMINDER_SENT,
            user=subscription.user,
            metadata={"stripe_event_id": event_id} if event_id else {},
            dedupe_key=dedupe_key,
        )
        return True

    @staticmethod
    def _activate_pass_from_stripe(data_object):
        """Grant a fixed-length Pro entitlement for a paid one-time pass."""
        from ..models import AssistantSubscription

        user_id = (data_object.get("metadata") or {}).get("user_id")
        subscription = AssistantSubscription.objects.filter(user_id=user_id).select_related("user").first()
        if not subscription:
            logger.warning("Pro pass payment references unknown user_id=%s", user_id)
            return
        PaymentService._grant_pass(
            subscription,
            session_id=data_object.get("id") or "",
            customer_id=data_object.get("customer") or "",
            source=(data_object.get("metadata") or {}).get("source", ""),
        )

    @staticmethod
    def _grant_pass(subscription, *, session_id, customer_id="", source=""):
        """Idempotently extend Pro by the pass length for one paid session."""
        from ..models import AssistantSubscription, ConsumerFunnelEvent

        dedupe_key = f"pro-pass-payment:{session_id}"
        with transaction.atomic():
            subscription = AssistantSubscription.objects.select_for_update().select_related("user").get(pk=subscription.pk)
            if ConsumerFunnelEvent.objects.filter(dedupe_key=dedupe_key).exists():
                return subscription
            ConsumerFunnelEvent.objects.create(
                event=ConsumerFunnelEvent.Event.PAYMENT_COMPLETED,
                user=subscription.user,
                metadata={
                    "plan": "pass",
                    "checkout_session_id": session_id,
                    **({"source": source} if source else {}),
                },
                dedupe_key=dedupe_key,
            )
            now = timezone.now()
            start = subscription.expires_at if subscription.is_pro_active() and subscription.expires_at else now
            subscription.is_subscribed = True
            subscription.subscribed_at = subscription.subscribed_at or now
            subscription.expires_at = start + timedelta(days=PRO_PLANS["pass"]["days"])
            subscription.stripe_checkout_session_id = ""
            subscription.stripe_checkout_expires_at = None
            if customer_id:
                subscription.stripe_customer_id = customer_id
            subscription.save()
            return subscription

    @staticmethod
    def _activate_assistant_from_stripe(data_object):
        """Apply subscription state from a webhook without requiring a return visit."""
        from ..models import AssistantSubscription, ConsumerFunnelEvent
        from .funnel import record_consumer_event

        metadata = data_object.get("metadata") or {}
        user_id = metadata.get("user_id")
        subscription_id = data_object.get("subscription") or data_object.get("id")
        if not user_id or not subscription_id:
            logger.warning("Assistant subscription event missing user_id or subscription id")
            return
        try:
            subscription = AssistantSubscription.objects.get(user_id=user_id)
        except AssistantSubscription.DoesNotExist:
            logger.warning("Assistant subscription event references unknown user_id=%s", user_id)
            return
        period_end = data_object.get("current_period_end")
        if not period_end:
            item_periods = [
                item.get("current_period_end")
                for item in ((data_object.get("items") or {}).get("data") or [])
                if item.get("current_period_end")
            ]
            period_end = max(item_periods, default=None)
        expires_at = (
            datetime.fromtimestamp(period_end, tz=dt_timezone.utc)
            if period_end
            else None
        )
        # Checkout webhooks do not always include the subscription period.
        # Keep the browser-return fallback instead of granting an indefinite
        # entitlement or overwriting an already-known expiry with NULL.
        expires_at = expires_at or subscription.expires_at or (
            timezone.now() + timedelta(days=32)
        )
        AssistantSubscription.objects.filter(pk=subscription.pk).update(
            is_subscribed=True,
            stripe_subscription_id=subscription_id,
            stripe_customer_id=data_object.get("customer") or subscription.stripe_customer_id,
            stripe_checkout_session_id="",
            stripe_checkout_expires_at=None,
            subscribed_at=subscription.subscribed_at or timezone.now(),
            expires_at=expires_at,
        )
        is_trial = (
            data_object.get("status") == "trialing"
            or data_object.get("payment_status") == "no_payment_required"
        )
        record_consumer_event(
            ConsumerFunnelEvent.Event.PAYMENT_COMPLETED,
            user=subscription.user,
            metadata={
                "stripe_subscription_id": str(subscription_id),
                "plan": metadata.get("plan", "monthly"),
                **({"trial": True} if is_trial else {}),
                **({"source": metadata["source"]} if metadata.get("source") else {}),
            },
            dedupe_key=f"pro-payment:{subscription_id}",
        )

    @staticmethod
    def _mark_job_paid(job: Job, *, session_id: str, payment_intent: str):
        """Activate the job for paid posting. Idempotent."""
        with transaction.atomic():
            job = Job.objects.select_for_update().get(pk=job.pk)
            if job.is_paid and job.is_active:
                updates = {}
                if not job.stripe_checkout_session_id and session_id:
                    updates["stripe_checkout_session_id"] = session_id
                if not job.stripe_payment_intent and payment_intent:
                    updates["stripe_payment_intent"] = payment_intent
                if updates:
                    Job.objects.filter(pk=job.pk).update(**updates)
                return

            now = timezone.now()
            Job.objects.filter(pk=job.pk).update(
                is_paid=True,
                is_active=True,
                paid_at=now,
                # A draft can sit unpaid for days; the listing starts now.
                posted_at=now,
                featured_until=max(job.featured_until or now, now) + timedelta(days=JOB_POSTING_DURATION_DAYS),
                stripe_checkout_session_id=session_id or job.stripe_checkout_session_id,
                stripe_payment_intent=payment_intent or job.stripe_payment_intent,
                expires_at=now + timezone.timedelta(days=JOB_POSTING_DURATION_DAYS),
            )
            from ..models import EmployerFunnelEvent
            from .funnel import record_employer_event
            record_employer_event(
                EmployerFunnelEvent.Event.PAYMENT_COMPLETED,
                user=job.poster,
                job=job,
            )

    # ------------------------------------------------------------------
    # Featured placement for an existing listing
    # ------------------------------------------------------------------

    @staticmethod
    def create_feature_checkout_session(job: Job, domain_url: str, *, user=None, source=""):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        metadata = {"product": "feature", "job_id": str(job.id)}
        if user is not None and getattr(user, "is_authenticated", False):
            metadata["user_id"] = str(user.id)
        if source:
            metadata["source"] = " ".join(str(source).split())[:80]
        feature_path = reverse("jobs:feature_job", kwargs={"slug": job.slug})
        session = stripe.checkout.Session.create(
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": FEATURE_PRICE_USD_CENTS,
                    "product_data": {
                        "name": f"Featured listing: {job.title}"[:250],
                        "description": f"{FEATURE_DURATION_DAYS} days of featured placement on Remote Impact",
                    },
                },
                "quantity": 1,
            }],
            mode="payment",
            invoice_creation={"enabled": True},
            allow_promotion_codes=True,
            customer_email=getattr(user, "email", None) or None,
            success_url=f"{domain_url}{reverse('jobs:feature_job_success', kwargs={'slug': job.slug})}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{domain_url}{feature_path}?cancelled=1",
            metadata=metadata,
            payment_intent_data={"metadata": metadata},
            client_reference_id=str(job.id),
        )
        return session

    @staticmethod
    def verify_feature_session(session_id: str, job: Job):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            session = stripe.checkout.Session.retrieve(session_id)
        except stripe.error.StripeError:
            logger.exception("Stripe feature session retrieve failed: %s", session_id)
            return None
        metadata = session.metadata or {}
        if session.payment_status != "paid" or metadata.get("product") != "feature" or str(metadata.get("job_id")) != str(job.id):
            return None
        return PaymentService._activate_feature({
            "id": session.id,
            "amount_total": getattr(session, "amount_total", 0),
            "customer_details": getattr(session, "customer_details", None) or {},
            "metadata": dict(metadata),
        })

    @staticmethod
    def _activate_feature(data_object):
        """Idempotently extend a job's featured window for one paid session."""
        from ..models import EmployerFunnelEvent, JobFeaturePurchase
        from .funnel import record_employer_event

        metadata = data_object.get("metadata") or {}
        session_id = data_object.get("id") or ""
        try:
            job = Job.objects.get(pk=metadata.get("job_id"))
        except (Job.DoesNotExist, ValueError, TypeError):
            logger.warning("Feature payment %s references unknown job", session_id)
            return None
        with transaction.atomic():
            job = Job.objects.select_for_update().get(pk=job.pk)
            existing = JobFeaturePurchase.objects.filter(stripe_checkout_session_id=session_id).first()
            if existing:
                return existing
            now = timezone.now()
            until = max(job.featured_until or now, now) + timedelta(days=FEATURE_DURATION_DAYS)
            Job.objects.filter(pk=job.pk).update(featured_until=until)
            details = data_object.get("customer_details") or {}
            purchase = JobFeaturePurchase.objects.create(
                job=job,
                stripe_checkout_session_id=session_id,
                amount_cents=data_object.get("amount_total") or FEATURE_PRICE_USD_CENTS,
                purchaser_email=(details.get("email") if hasattr(details, "get") else "") or "",
                featured_until=until,
            )
        record_employer_event(
            EmployerFunnelEvent.Event.PAYMENT_COMPLETED, job=job,
            metadata={"product": "feature", "amount_cents": purchase.amount_cents},
        )
        return purchase

    # ------------------------------------------------------------------
    # Bundled Remote Impact Pro subscription
    # ------------------------------------------------------------------

    @staticmethod
    def create_pro_subscription_session(
        user,
        domain_url: str,
        *,
        success_path="/resources/applicant-assistant/subscribe/success/",
        cancel_path="/resources/applicant-assistant/",
        source="",
        return_session=False,
        plan=DEFAULT_PRO_PLAN,
    ):
        """Create a Stripe checkout session for Remote Impact Pro.

        ``monthly`` is a $10/month subscription with a free trial for first-time
        subscribers; ``pass`` is a one-time payment for three months of Pro.
        """
        from ..models import AssistantSubscription

        stripe.api_key = settings.STRIPE_SECRET_KEY
        plan = normalize_pro_plan(plan)
        plan_config = PRO_PLANS[plan]

        subscription = AssistantSubscription.get_or_create_for_user(user)

        if subscription.stripe_checkout_session_id:
            try:
                existing = stripe.checkout.Session.retrieve(
                    subscription.stripe_checkout_session_id
                )
                existing_metadata = getattr(existing, "metadata", None)
                existing_plan = (
                    existing_metadata.get("plan") if isinstance(existing_metadata, dict) else None
                ) or DEFAULT_PRO_PLAN
                if existing.status == "open" and existing.url and existing_plan == plan:
                    return existing if return_session else existing.url
            except stripe.error.StripeError:
                logger.info(
                    "Could not reuse Pro checkout %s; creating a new one",
                    subscription.stripe_checkout_session_id,
                )

        if subscription.stripe_customer_id:
            customer_id = subscription.stripe_customer_id
        else:
            customer = stripe.Customer.create(
                email=user.email,
                metadata={"user_id": user.id},
                idempotency_key=f"pro-customer-{user.id}",
            )
            customer_id = customer.id
            subscription.stripe_customer_id = customer_id
            subscription.save()

        source = " ".join(str(source or "").split())[:80]
        checkout_metadata = {"user_id": str(user.id), "plan": plan}
        if source:
            checkout_metadata["source"] = source

        price_data = {
            "currency": "usd",
            "unit_amount": plan_config["unit_amount"],
            "product_data": {
                "name": "Remote Impact Pro" if plan == "monthly" else "Remote Impact Pro - 3-month Job Search Pass",
                "description": plan_config["description"],
            },
        }
        session_kwargs = {}
        if plan == "monthly":
            price_data["recurring"] = {"interval": plan_config["interval"]}
            subscription_data = {"metadata": checkout_metadata}
            if pro_trial_eligible(subscription):
                subscription_data["trial_period_days"] = PRO_TRIAL_DAYS
            session_kwargs.update(mode="subscription", subscription_data=subscription_data)
        else:
            session_kwargs.update(
                mode="payment",
                payment_intent_data={"metadata": checkout_metadata},
            )

        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            line_items=[{"price_data": price_data, "quantity": 1}],
            success_url=f"{domain_url}{success_path}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{domain_url}{cancel_path}",
            metadata=checkout_metadata,
            client_reference_id=str(user.id),
            after_expiration={"recovery": {"enabled": True}},
            allow_promotion_codes=True,
            idempotency_key=(
                f"pro-checkout-{user.id}-{plan}-{timezone.localdate().isoformat()}"
            ),
            **session_kwargs,
        )
        AssistantSubscription.objects.filter(pk=subscription.pk).update(
            stripe_checkout_session_id=checkout_session.id,
            stripe_checkout_expires_at=datetime.fromtimestamp(
                checkout_session.expires_at, tz=dt_timezone.utc
            ),
        )
        return checkout_session if return_session else checkout_session.url

    @staticmethod
    def create_assistant_subscription_session(user, domain_url: str):
        """Backward-compatible checkout entry point for the application assistant."""
        return PaymentService.create_pro_subscription_session(user, domain_url)

    @staticmethod
    def create_billing_portal_session(user, domain_url: str):
        """Open Stripe's hosted subscription management for an existing customer."""
        from ..models import AssistantSubscription

        stripe.api_key = settings.STRIPE_SECRET_KEY
        subscription = AssistantSubscription.get_or_create_for_user(user)
        if not subscription.stripe_customer_id:
            raise ValueError("This account does not have a Stripe customer yet")
        configurations = stripe.billing_portal.Configuration.list(active=True, limit=1)
        if not configurations.data:
            raise ValueError("Stripe billing portal is not configured")
        session = stripe.billing_portal.Session.create(
            customer=subscription.stripe_customer_id,
            return_url=f"{domain_url}{reverse('jobs:account')}",
            configuration=configurations.data[0].id,
        )
        return session.url

    @staticmethod
    def verify_assistant_subscription(session_id: str, user):
        """Verify and activate assistant subscription after checkout."""
        from datetime import timedelta

        from ..models import AssistantSubscription, ConsumerFunnelEvent
        from .funnel import record_consumer_event

        stripe.api_key = settings.STRIPE_SECRET_KEY

        try:
            session = stripe.checkout.Session.retrieve(session_id)
        except stripe.error.StripeError:
            logger.exception("Stripe assistant session retrieve failed: %s", session_id)
            return False, None

        metadata = session.metadata or {}
        is_pass = metadata.get("plan") == "pass"
        if session.payment_status not in ("paid", "no_payment_required"):
            return False, None
        if not is_pass and not session.subscription:
            return False, None

        if str(metadata.get("user_id")) != str(user.id):
            logger.warning(
                "Assistant session %s user_id mismatch (got %s, expected %s)",
                session_id,
                (session.metadata or {}).get("user_id"),
                user.id,
            )
            return False, None

        subscription = AssistantSubscription.get_or_create_for_user(user)
        if is_pass:
            if session.payment_status != "paid":
                return False, None
            subscription = PaymentService._grant_pass(
                subscription,
                session_id=session.id,
                customer_id=getattr(session, "customer", "") or "",
                source=metadata.get("source", ""),
            )
            return True, subscription
        subscription.is_subscribed = True
        subscription.stripe_subscription_id = session.subscription
        subscription.stripe_checkout_session_id = ""
        subscription.stripe_checkout_expires_at = None
        subscription.subscribed_at = timezone.now()
        subscription.expires_at = timezone.now() + timedelta(days=32)
        subscription.save()
        record_consumer_event(
            ConsumerFunnelEvent.Event.PAYMENT_COMPLETED,
            user=user,
            metadata={
                "stripe_subscription_id": str(session.subscription),
                "checkout_session_id": session_id,
                **(
                    {"source": (session.metadata or {}).get("source")}
                    if (session.metadata or {}).get("source")
                    else {}
                ),
            },
            dedupe_key=f"pro-payment:{session.subscription}",
        )
        return True, subscription
