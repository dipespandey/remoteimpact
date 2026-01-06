import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from ..models import Job

User = get_user_model()


class PaymentService:
    @staticmethod
    def create_checkout_session(job: Job, domain_url: str):
        stripe.api_key = settings.STRIPE_SECRET_KEY

        try:
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[
                    {
                        "price_data": {
                            "currency": "usd",
                            "unit_amount": 10000,  # $100.00
                            "product_data": {
                                "name": f"Job Posting: {job.title}",
                                "description": "30-day job listing on Remote Impact",
                            },
                        },
                        "quantity": 1,
                    },
                ],
                mode="payment",
                success_url=f"{domain_url}/jobs/payment/success/?session_id={{CHECKOUT_SESSION_ID}}&job_id={job.id}",
                cancel_url=f"{domain_url}/jobs/payment/cancel/?job_id={job.id}",
                metadata={"job_id": job.id},
            )
            return checkout_session.url
        except Exception as e:
            # Log error
            raise e

    @staticmethod
    def verify_payment(session_id: str, job_id: str):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session.payment_status == "paid":
                job = Job.objects.get(id=job_id)
                if not job.is_paid:
                    job.is_paid = True
                    job.is_active = True
                    job.stripe_payment_intent = session.payment_intent
                    job.save()
                    return True, job
            return False, None
        except Exception:
            return False, None

    @staticmethod
    def create_assistant_subscription_session(user, domain_url: str):
        """Create a Stripe checkout session for Assistant Pro subscription ($5/month)."""
        from ..models import AssistantSubscription

        stripe.api_key = settings.STRIPE_SECRET_KEY

        # Get or create subscription record
        subscription = AssistantSubscription.get_or_create_for_user(user)

        try:
            # Create or get Stripe customer
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
                            "unit_amount": 500,  # $5.00
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
        except Exception as e:
            raise e

    @staticmethod
    def verify_assistant_subscription(session_id: str, user):
        """Verify and activate assistant subscription after checkout."""
        from ..models import AssistantSubscription
        from django.utils import timezone
        from datetime import timedelta

        stripe.api_key = settings.STRIPE_SECRET_KEY

        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session.payment_status == "paid" and session.subscription:
                subscription = AssistantSubscription.get_or_create_for_user(user)
                subscription.is_subscribed = True
                subscription.stripe_subscription_id = session.subscription
                subscription.subscribed_at = timezone.now()
                # Set expiry to 1 month from now (Stripe will handle actual billing)
                subscription.expires_at = timezone.now() + timedelta(days=32)
                subscription.save()
                return True, subscription
            return False, None
        except Exception:
            return False, None
