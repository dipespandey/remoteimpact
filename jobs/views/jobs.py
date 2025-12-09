from django.views.generic import ListView, DetailView, FormView, View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.http import JsonResponse
from django.contrib import messages
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST

from ..models import Job, Category
from ..forms import JobSubmissionForm
from ..services.job_service import JobService
from ..services.payment_service import PaymentService


class JobListView(ListView):
    model = Job
    template_name = "jobs/job_list.html"
    context_object_name = "jobs"
    paginate_by = 20

    def get_queryset(self):
        return JobService.get_filtered_jobs(self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        categories = Category.objects.all()
        context["categories"] = categories

        # Current filters
        current_category = self.request.GET.get("category")
        context["current_category"] = current_category
        context["current_type"] = self.request.GET.get("type")
        context["search_query"] = self.request.GET.get("q", "")

        # Get category object for display
        if current_category:
            context["current_category_obj"] = categories.filter(slug=current_category).first()

        # Filter values from request
        context["filters"] = {
            "country": self.request.GET.get("country", ""),
            "organization": self.request.GET.get("organization", ""),
            "salary_min": self.request.GET.get("salary_min", ""),
            "salary_max": self.request.GET.get("salary_max", ""),
            "experience": self.request.GET.get("experience", ""),
            "education": self.request.GET.get("education", ""),
        }

        # Get distinct countries and organizations for dropdowns
        from ..models import Organization
        context["countries"] = Job.objects.filter(is_active=True).exclude(
            location__isnull=True
        ).exclude(location="").values_list("location", flat=True).distinct().order_by("location")[:100]
        context["organizations"] = Organization.objects.filter(
            jobs__is_active=True
        ).distinct().order_by("name")[:100]

        # Knowledge-based filters
        context["knowledge_filters"] = {
            "experience_levels": ["Entry Level", "Mid Level", "Senior", "Executive", "Internship"],
            "education_levels": ["High School", "Associate", "Bachelor's", "Master's", "PhD"],
        }

        return context


class JobDetailView(DetailView):
    model = Job
    template_name = "jobs/job_detail.html"
    context_object_name = "job"

    def get_queryset(self):
        return Job.objects.filter(is_active=True).select_related("organization")


class PostJobView(LoginRequiredMixin, FormView):
    template_name = "jobs/post_job.html"
    form_class = JobSubmissionForm

    def form_valid(self, form):
        job = JobService.create_job(form.cleaned_data, user=self.request.user)

        try:
            domain_url = self.request.build_absolute_uri("/")[:-1]
            checkout_url = PaymentService.create_checkout_session(job, domain_url)
            return redirect(checkout_url)
        except Exception as e:
            messages.error(self.request, f"Error creating payment session: {str(e)}")
            return redirect("jobs:home")


class SaveJobView(LoginRequiredMixin, View):
    def post(self, request, slug):
        saved = JobService.toggle_save_job(request.user, slug)
        return JsonResponse({"saved": saved})
