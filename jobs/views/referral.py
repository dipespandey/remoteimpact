from django.views.generic import TemplateView, View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.conf import settings

from ..models import Referral, ReferralSignup


class ReferralDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "jobs/referral_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        referral = Referral.get_or_create_for_user(self.request.user)
        context["referral"] = referral
        context["referral_link"] = f"{settings.SITE_URL}/?ref={referral.code}"
        context["signups"] = referral.signups.select_related("referred_user").order_by("-created_at")[:50]
        context["total_referrals"] = referral.signups.count()
        return context


class ReferralLandingView(View):
    """Handles ?ref=CODE on any page - stores code in session."""

    def get(self, request, *args, **kwargs):
        code = request.GET.get("ref")
        if code:
            request.session["referral_code"] = code
        return redirect("jobs:home")
