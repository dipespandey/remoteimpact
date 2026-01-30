from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from ..models import SeekerProfile


class VisibilitySettingsView(LoginRequiredMixin, TemplateView):
    template_name = "account/visibility_settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context["seeker_profile"] = self.request.user.seeker_profile
        except SeekerProfile.DoesNotExist:
            context["seeker_profile"] = None
        context["visibility_choices"] = SeekerProfile.Visibility.choices
        return context

    def post(self, request, *args, **kwargs):
        try:
            seeker = request.user.seeker_profile
        except SeekerProfile.DoesNotExist:
            return redirect("jobs:account")
        new_vis = request.POST.get("visibility")
        if new_vis in dict(SeekerProfile.Visibility.choices):
            seeker.visibility = new_vis
            seeker.save(update_fields=["visibility"])
        return redirect("jobs:visibility_settings")
