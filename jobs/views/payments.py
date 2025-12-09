from django.views.generic import View, RedirectView
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from ..services.payment_service import PaymentService


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
        else:
            messages.error(request, "Payment verification failed.")
            return redirect("jobs:home")


class PaymentCancelView(RedirectView):
    pattern_name = "jobs:home"

    def get(self, request, *args, **kwargs):
        messages.warning(
            request, "Payment cancelled. Your job has been saved as a draft."
        )
        return super().get(request, *args, **kwargs)
