from django.views.generic import ListView, CreateView, DeleteView, View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.http import JsonResponse

from ..models import JobAlert, Category
from ..forms import JobAlertForm


class JobAlertListView(LoginRequiredMixin, ListView):
    template_name = "jobs/job_alerts.html"
    context_object_name = "alerts"

    def get_queryset(self):
        return JobAlert.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = JobAlertForm()
        return context


class JobAlertCreateView(LoginRequiredMixin, CreateView):
    model = JobAlert
    form_class = JobAlertForm
    template_name = "jobs/job_alert_create.html"

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("jobs:job_alerts")


class JobAlertDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        alert = get_object_or_404(JobAlert, pk=pk, user=request.user)
        alert.delete()
        return redirect("jobs:job_alerts")


class JobAlertToggleView(LoginRequiredMixin, View):
    def post(self, request, pk):
        alert = get_object_or_404(JobAlert, pk=pk, user=request.user)
        alert.is_active = not alert.is_active
        alert.save(update_fields=["is_active"])
        return redirect("jobs:job_alerts")
