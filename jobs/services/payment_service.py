import stripe
from django.conf import settings
from ..models import Job


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
