from django.views.generic import FormView, RedirectView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy

from ..forms import OnboardingTypeForm, EmployerOnboardingForm, SeekerOnboardingForm
from ..models import UserProfile
from ..services.onboarding_service import OnboardingService


class StartOnboardingView(LoginRequiredMixin, RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        target = OnboardingService.get_redirect_for_state(self.request.user)
        if target:
            return reverse_lazy(target)
        return reverse_lazy("jobs:account")


class OnboardingSelectTypeView(LoginRequiredMixin, FormView):
    template_name = "jobs/onboarding/select_type.html"
    form_class = OnboardingTypeForm
    success_url = reverse_lazy("jobs:start_onboarding")

    def form_valid(self, form):
        OnboardingService.set_account_type(
            self.request.user, form.cleaned_data["account_type"]
        )
        return super().form_valid(form)


class OnboardingEmployerView(LoginRequiredMixin, FormView):
    template_name = "jobs/onboarding/employer_form.html"
    form_class = EmployerOnboardingForm
    success_url = reverse_lazy("jobs:account")

    def dispatch(self, request, *args, **kwargs):
        profile = OnboardingService.get_user_profile(request.user)
        if not profile or profile.account_type != UserProfile.AccountType.EMPLOYER:
            return redirect("jobs:start_onboarding")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        OnboardingService.create_organization(self.request.user, form)
        return super().form_valid(form)


class OnboardingSeekerView(LoginRequiredMixin, FormView):
    template_name = "jobs/onboarding/seeker_form.html"
    form_class = SeekerOnboardingForm
    success_url = reverse_lazy("jobs:account")

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

    def form_valid(self, form):
        form.save()
        return super().form_valid(form)
