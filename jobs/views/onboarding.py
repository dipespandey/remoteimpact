from urllib.parse import urlencode, urlparse

from django.contrib import messages
from django.views.generic import FormView, RedirectView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from ..forms import OnboardingTypeForm, EmployerOnboardingForm, SeekerOnboardingForm, OrgImpactProfileForm
from ..models import UserProfile, Organization
from ..services.onboarding_service import OnboardingService
from ..services.org_signals_service import OrgSignalsService


def _safe_next_url(request):
    next_url = request.GET.get("next", "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return ""


def _append_next(url, next_url):
    if not next_url:
        return url
    return f"{url}?{urlencode({'next': next_url})}"


class StartOnboardingView(LoginRequiredMixin, RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        target = OnboardingService.get_redirect_for_state(self.request.user)
        next_url = _safe_next_url(self.request)

        # Job-seeking signups have already expressed their intent by arriving
        # here from the Scout flow, or have no destination yet. Do not make
        # them choose an account type before receiving their first result.
        # Employer and job-specific flows retain the account-type step.
        if target == "jobs:onboarding_select_type" and self.request.GET.get("intent") != "employer":
            next_path = urlparse(next_url).path if next_url else ""
            if not next_url or next_path == reverse("jobs:job_alerts"):
                return next_url or f"{reverse('jobs:job_alerts')}?source=signup"

        if target:
            url = reverse(target)
            return _append_next(url, next_url)

        # Onboarding complete - redirect to next URL or account
        if next_url:
            return next_url
        return reverse_lazy("jobs:account")


class OnboardingSelectTypeView(LoginRequiredMixin, FormView):
    template_name = "jobs/onboarding/select_type.html"
    form_class = OnboardingTypeForm

    def get(self, request, *args, **kwargs):
        # Auto-select employer if intent=employer is in URL (from Partner with us button)
        intent = request.GET.get("intent", "")
        if intent == "employer":
            OnboardingService.set_account_type(
                request.user, UserProfile.AccountType.EMPLOYER
            )
            next_url = _safe_next_url(request)
            return redirect(_append_next(reverse("jobs:start_onboarding"), next_url))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next_url"] = _safe_next_url(self.request)
        context["came_from_job"] = "/jobs/" in context["next_url"]
        return context

    def get_success_url(self):
        next_url = _safe_next_url(self.request)
        base_url = reverse("jobs:start_onboarding")
        return _append_next(base_url, next_url)

    def form_valid(self, form):
        OnboardingService.set_account_type(
            self.request.user, form.cleaned_data["account_type"]
        )
        return super().form_valid(form)


class OnboardingEmployerView(LoginRequiredMixin, FormView):
    template_name = "jobs/onboarding/employer_form.html"
    form_class = EmployerOnboardingForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        profile = OnboardingService.get_user_profile(request.user)
        if not profile or profile.account_type != UserProfile.AccountType.EMPLOYER:
            return redirect("jobs:start_onboarding")
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        # After creating org, go to impact profile step
        next_url = _safe_next_url(self.request)
        base_url = reverse("jobs:onboarding_impact_profile")
        return _append_next(base_url, next_url)

    def form_valid(self, form):
        org = OnboardingService.create_organization(self.request.user, form)
        # Run auto-detection for org signals
        OrgSignalsService.update_org_signals(org)
        return super().form_valid(form)


class EditCompanyProfileView(LoginRequiredMixin, UpdateView):
    """Edit the current user's existing organization (name/website/bio)."""

    template_name = "jobs/onboarding/company_edit.html"
    form_class = EmployerOnboardingForm
    success_url = reverse_lazy("jobs:account")

    def get_object(self, queryset=None):
        org = self.request.user.organizations.first()
        if not org:
            raise Http404("No organization to edit")
        return org

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Company profile updated.")
        return response


class OnboardingImpactProfileView(LoginRequiredMixin, FormView):
    """Step 2 of employer onboarding: Complete organization impact profile."""

    template_name = "jobs/onboarding/impact_profile.html"
    form_class = OrgImpactProfileForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        profile = OnboardingService.get_user_profile(request.user)
        if not profile or profile.account_type != UserProfile.AccountType.EMPLOYER:
            return redirect("jobs:start_onboarding")
        if not request.user.organizations.exists():
            return redirect("jobs:onboarding_employer")
        return super().dispatch(request, *args, **kwargs)

    def get_organization(self):
        return self.request.user.organizations.first()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_organization()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        org = self.get_organization()
        context["organization"] = org
        context["next_url"] = _safe_next_url(self.request)
        # Get auto-detected signals for display
        context["signals_summary"] = OrgSignalsService.get_signals_summary(org)
        return context

    def get_success_url(self):
        next_url = _safe_next_url(self.request)
        if next_url:
            return next_url
        return reverse_lazy("jobs:account")

    def form_valid(self, form):
        org = form.save(commit=False)
        org.impact_profile_completed_at = timezone.now()
        org.save()
        return super().form_valid(form)


class OnboardingSeekerView(LoginRequiredMixin, FormView):
    template_name = "jobs/onboarding/seeker_form.html"
    form_class = SeekerOnboardingForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Bind to existing profile instance
        kwargs["instance"] = self.request.user.profile
        return kwargs

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        profile = OnboardingService.get_user_profile(request.user)
        if not profile or profile.account_type != UserProfile.AccountType.SEEKER:
            return redirect("jobs:start_onboarding")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next_url"] = _safe_next_url(self.request)
        return context

    def get_success_url(self):
        # After seeker form, route to Impact Profile wizard
        next_url = _safe_next_url(self.request)
        base_url = reverse("jobs:impact_wizard")
        return _append_next(base_url, next_url)

    def form_valid(self, form):
        form.save()
        return super().form_valid(form)
