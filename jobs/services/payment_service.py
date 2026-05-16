import logging

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from ..models import Job

User = get_user_model()
logger = logging.getLogger(__name__)


JOB_POSTING_PRICE_USD_CENTS = 10000  # $100.00
JOB_POSTING_DURATION_DAYS = 30


class PaymentService:
    @staticmethod
    def create_checkout_session(job: Job, domain_url: str):
        """Create a Stripe Checkout Session for a job posting and return its URL.

        Persists the session id on the Job so the webhook can correlate the
        completion event back even if the user never returns from Stripe.
        """
        stripe.api_key = settings.STRIPE_SECRET_KEY

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
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
        except Job.DoesNotExist:
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
        data_object = event.get("data", {}).get("object", {}) or {}

        if event_type in (
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        ):
            if data_object.get("payment_status") != "paid":
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
            "checkout.session.expired",
            "checkout.session.async_payment_failed",
        ):
            logger.info(
                "Stripe checkout %s for session %s",
                event_type,
                data_object.get("id"),
            )
            return

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
                stripe_checkout_session_id=session_id or job.stripe_checkout_session_id,
                stripe_payment_intent=payment_intent or job.stripe_payment_intent,
                expires_at=now + timezone.timedelta(days=JOB_POSTING_DURATION_DAYS),
            )

    # ------------------------------------------------------------------
    # Assistant Pro subscription (existing flow, preserved)
    # ------------------------------------------------------------------

    @staticmethod
    def create_assistant_subscription_session(user, domain_url: str):
        """Create a Stripe checkout session for Assistant Pro subscription ($5/month)."""
        from ..models import AssistantSubscription

        stripe.api_key = settings.STRIPE_SECRET_KEY

        subscription = AssistantSubscription.get_or_create_for_user(user)

        if subscription.stripe_customer_id:
            customer_id = subscription.stripe_customer_id
        else:
            customer = stripe.Customer.create(
                email=user.email,
                metadata={"user_id": user.id},
            )
            customer_id = customer.id
            subscription.stripe_customer_id = customer_id
            subscription.save()

        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": 500,
                        "recurring": {"interval": "month"},
                        "product_data": {
                            "name": "Applicant Assistant Pro",
                            "description": "Unlimited cover letters and interview prep",
                        },
                    },
                    "quantity": 1,
                },
            ],
            mode="subscription",
            success_url=f"{domain_url}/resources/applicant-assistant/subscribe/success/?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{domain_url}/resources/applicant-assistant/",
            metadata={"user_id": user.id},
        )
        return checkout_session.url

    @staticmethod
    def verify_assistant_subscription(session_id: str, user):
        """Verify and activate assistant subscription after checkout."""
        from datetime import timedelta

        from ..models import AssistantSubscription

        stripe.api_key = settings.STRIPE_SECRET_KEY

        try:
            session = stripe.checkout.Session.retrieve(session_id)
        except stripe.error.StripeError:
            logger.exception("Stripe assistant session retrieve failed: %s", session_id)
            return False, None

        if session.payment_status != "paid" or not session.subscription:
            return False, None

        if str((session.metadata or {}).get("user_id")) != str(user.id):
            logger.warning(
                "Assistant session %s user_id mismatch (got %s, expected %s)",
                session_id,
                (session.metadata or {}).get("user_id"),
                user.id,
            )
            return False, None

        subscription = AssistantSubscription.get_or_create_for_user(user)
        subscription.is_subscribed = True
        subscription.stripe_subscription_id = session.subscription
        subscription.subscribed_at = timezone.now()
        subscription.expires_at = timezone.now() + timedelta(days=32)
        subscription.save()
        return True, subscription
