import logging

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import FormView, RedirectView, View

from ..forms import JobSubmissionForm
from ..models import Job
from ..services.job_service import JobService
from ..services.payment_service import PaymentService

logger = logging.getLogger(__name__)


class PaymentSuccessView(View):
    def get(self, request, *args, **kwargs):
        session_id = request.GET.get("session_id")
        job_id = request.GET.get("job_id")

        if not session_id or not job_id:
            messages.error(request, "Invalid payment session.")
            return redirect("jobs:home")

        success, job = PaymentService.verify_payment(session_id, job_id)
        if success:
            messages.success(request, "Payment successful! Your job is now live.")
            return redirect("jobs:job_detail", slug=job.slug)

        messages.error(
            request,
            "We couldn't confirm your payment yet. If you completed checkout, "
            "your job will be activated automatically within a few minutes.",
        )
        return redirect("jobs:home")


class JobCheckoutView(LoginRequiredMixin, FormView):
    """Edit-then-pay flow for a draft job: GET shows the post-job form
    pre-filled from the saved draft, POST saves edits and redirects to Stripe.
    """

    template_name = "jobs/post_job.html"
    form_class = JobSubmissionForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.job = get_object_or_404(Job, slug=kwargs["slug"], poster=request.user)
        if self.job.is_paid:
            return redirect("jobs:job_detail", slug=self.job.slug)
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        job = self.job
        raw = job.raw_data or {}
        return {
            "organization_name": job.organization.name,
            "organization_website": job.organization.website,
            "organization_description": job.organization.description,
            "title": job.title,
            "category": job.category_id,
            "job_type": job.job_type or "full_time",
            "location": job.location,
            "description": job.description,
            "requirements": job.requirements,
            "impact": raw.get("impact", ""),
            "benefits": raw.get("benefits", ""),
            "salary_currency": job.salary_currency or "USD",
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            "application_url": job.application_url,
            "application_email": job.application_email,
            "how_to_apply": raw.get("how_to_apply", ""),
            "contact_email": raw.get("internal_contact", ""),
            "start_timeline": raw.get("start_timeline", ""),
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_edit"] = True
        context["job"] = self.job
        return context

    def form_valid(self, form):
        JobService.update_job(self.job, form.cleaned_data)
        try:
            domain_url = self.request.build_absolute_uri("/")[:-1]
            checkout_url = PaymentService.create_checkout_session(self.job, domain_url)
        except Exception as exc:
            logger.exception("Resume-checkout failed for job %s", self.job.id)
            messages.error(self.request, f"Could not start payment: {exc}")
            return redirect("jobs:account")
        return redirect(checkout_url)


class PaymentCancelView(RedirectView):
    pattern_name = "jobs:home"

    def get(self, request, *args, **kwargs):
        messages.warning(
            request, "Payment cancelled. Your job has been saved as a draft."
        )
        return super().get(request, *args, **kwargs)


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(require_POST, name="dispatch")
class StripeWebhookView(View):
    """Receives Stripe events at /webhook/stripe/.

    Verifies the Stripe signature using STRIPE_WEBHOOK_SECRET, then dispatches
    to PaymentService. Returns 200 quickly so Stripe doesn't retry on transient
    handler errors that we've already logged.
    """

    def post(self, request, *args, **kwargs):
        webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
        if not webhook_secret:
            logger.error("Stripe webhook received but STRIPE_WEBHOOK_SECRET is unset")
            return HttpResponse(status=503)

        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, webhook_secret
            )
        except ValueError:
            logger.warning("Stripe webhook received with invalid payload")
            return HttpResponseBadRequest("invalid payload")
        except stripe.error.SignatureVerificationError:
            logger.warning("Stripe webhook signature verification failed")
            return HttpResponseBadRequest("invalid signature")

        try:
            PaymentService.handle_webhook_event(event)
        except Exception:
            logger.exception(
                "Stripe webhook handler errored for event %s", event.get("id")
            )

        return HttpResponse(status=200)
