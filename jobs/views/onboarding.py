from urllib.parse import urlencode

from django.views.generic import FormView, RedirectView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy, reverse

from ..forms import OnboardingTypeForm, EmployerOnboardingForm, SeekerOnboardingForm
from ..models import UserProfile
from ..services.onboarding_service import OnboardingService


class StartOnboardingView(LoginRequiredMixin, RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        target = OnboardingService.get_redirect_for_state(self.request.user)
        next_url = self.request.GET.get("next", "")

        if target:
            url = reverse(target)
            if next_url:
                url = f"{url}?next={next_url}"
            return url

        # Onboarding complete - redirect to next URL or account
        if next_url:
            return next_url
        return reverse_lazy("jobs:account")


class OnboardingSelectTypeView(LoginRequiredMixin, FormView):
    template_name = "jobs/onboarding/select_type.html"
    form_class = OnboardingTypeForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next_url"] = self.request.GET.get("next", "")
        context["came_from_job"] = "/jobs/" in context["next_url"]
        return context

    def get_success_url(self):
        next_url = self.request.GET.get("next", "")
        base_url = reverse("jobs:start_onboarding")
        if next_url:
            return f"{base_url}?next={next_url}"
        return base_url

    def form_valid(self, form):
        OnboardingService.set_account_type(
            self.request.user, form.cleaned_data["account_type"]
        )
        return super().form_valid(form)


class OnboardingEmployerView(LoginRequiredMixin, FormView):
    template_name = "jobs/onboarding/employer_form.html"
    form_class = EmployerOnboardingForm

    def dispatch(self, request, *args, **kwargs):
        profile = OnboardingService.get_user_profile(request.user)
        if not profile or profile.account_type != UserProfile.AccountType.EMPLOYER:
            return redirect("jobs:start_onboarding")
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        next_url = self.request.GET.get("next", "")
        if next_url:
            return next_url
        return reverse_lazy("jobs:account")

    def form_valid(self, form):
        OnboardingService.create_organization(self.request.user, form)
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
        profile = OnboardingService.get_user_profile(request.user)
        if not profile or profile.account_type != UserProfile.AccountType.SEEKER:
            return redirect("jobs:start_onboarding")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next_url"] = self.request.GET.get("next", "")
        return context

    def get_success_url(self):
        next_url = self.request.GET.get("next", "")
        if next_url:
            return next_url
        return reverse_lazy("jobs:account")

    def form_valid(self, form):
        form.save()
        return super().form_valid(form)
