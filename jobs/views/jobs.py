from django.views.generic import ListView, DetailView, FormView, View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.http import JsonResponse, Http404
from django.contrib import messages
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.db.models import Q, Count

from ..models import Job, Category, SeekerProfile, Application
from ..forms import JobSubmissionForm
from ..services.job_service import JobService
from ..services.payment_service import PaymentService
from ..services.unified_matching_service import UnifiedMatchingService


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

        # Current filters - support multiple selections
        context["current_categories"] = self.request.GET.getlist("category")
        context["current_types"] = self.request.GET.getlist("type")
        context["search_query"] = self.request.GET.get("q", "")
        context["sort"] = self.request.GET.get("sort", "")

        # For backward compatibility with single category display
        current_category = self.request.GET.get("category")
        context["current_category"] = current_category
        if current_category:
            context["current_category_obj"] = categories.filter(slug=current_category).first()

        # Filter values from request - support multiple selections
        context["filters"] = {
            "countries": self.request.GET.getlist("country"),
            "organizations": self.request.GET.getlist("organization"),
            "salary_min": self.request.GET.get("salary_min", ""),
            "salary_max": self.request.GET.get("salary_max", ""),
            "experiences": self.request.GET.getlist("experience"),
            "educations": self.request.GET.getlist("education"),
            "posted": self.request.GET.get("posted", ""),
            # Keep single value versions for backward compatibility
            "country": self.request.GET.get("country", ""),
            "organization": self.request.GET.get("organization", ""),
            "experience": self.request.GET.get("experience", ""),
            "education": self.request.GET.get("education", ""),
        }

        # Max salary across all active jobs for the salary slider ceiling
        from django.db.models import Max
        max_salary_val = Job.objects.filter(is_active=True).aggregate(
            max_val=Max("salary_max")
        )["max_val"]
        context["max_salary"] = int(max_salary_val) if max_salary_val else 300000

        # Get distinct countries from the new country field (normalized)
        # Filter out 2-letter codes except UK/USA, and ensure proper country names
        from ..models import Organization

        # Valid short codes we want to keep
        valid_short_codes = {"UK", "USA"}

        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        cutoff = now - timedelta(days=180)
        countries_raw = (
            Job.objects.filter(is_active=True)
            .exclude(expires_at__lt=now)
            .exclude(expires_at__isnull=True, posted_at__lt=cutoff)
            .exclude(country__isnull=True)
            .exclude(country="")
            .values_list("country", flat=True)
            .distinct()
            .order_by("country")
        )

        # Filter: keep if length > 3, or if it's a valid short code (UK, USA)
        countries_from_field = sorted(set(
            c for c in countries_raw
            if len(c) > 3 or c.upper() in valid_short_codes
        ))[:100]

        context["countries"] = countries_from_field
        # Top 500 orgs sorted by job count
        context["organizations"] = Organization.objects.filter(
            jobs__is_active=True
        ).annotate(
            job_count=Count('jobs', filter=Q(jobs__is_active=True))
        ).order_by('-job_count', 'name')[:500]

        # Knowledge-based filters with field value mappings
        context["knowledge_filters"] = {
            "experience_levels": [
                {"display": "Entry Level", "value": "entry"},
                {"display": "Mid Level", "value": "mid"},
                {"display": "Senior", "value": "senior"},
                {"display": "Executive", "value": "executive"},
                {"display": "Internship", "value": "internship"},
            ],
            "education_levels": [
                {"display": "High School", "value": "high_school"},
                {"display": "Associate", "value": "associate"},
                {"display": "Bachelor's", "value": "bachelor"},
                {"display": "Master's", "value": "master"},
                {"display": "PhD", "value": "phd"},
            ],
            # Keep simple lists for backward compatibility
            "experience_levels_simple": ["Entry Level", "Mid Level", "Senior", "Executive", "Internship"],
            "education_levels_simple": ["High School", "Associate", "Bachelor's", "Master's", "PhD"],
        }

        # Job types for multiselect
        context["job_types"] = [
            {"display": "Full-time", "value": "full-time"},
            {"display": "Part-time", "value": "part-time"},
            {"display": "Contract", "value": "contract"},
            {"display": "Freelance", "value": "freelance"},
            {"display": "Internship", "value": "internship"},
        ]

        # Check if user has seeker profile for showing "My Matches" link
        context["seeker_profile"] = None
        if self.request.user.is_authenticated:
            try:
                seeker = SeekerProfile.objects.get(user=self.request.user)
                if seeker.wizard_completed:
                    context["seeker_profile"] = seeker
            except SeekerProfile.DoesNotExist:
                pass

        # Handle sort options
        sort = self.request.GET.get("sort")
        if sort == "salary-high":
            jobs = list(context.get("object_list", []))
            jobs.sort(key=lambda j: j.salary_max or 0, reverse=True)
            context["jobs"] = jobs
            context["object_list"] = jobs
        elif sort == "salary-low":
            jobs = list(context.get("object_list", []))
            jobs.sort(key=lambda j: j.salary_min or float("inf"))
            context["jobs"] = jobs
            context["object_list"] = jobs

        return context


class JobDetailView(DetailView):
    model = Job
    template_name = "jobs/job_detail.html"
    context_object_name = "job"

    def get(self, request, *args, **kwargs):
        try:
            return super().get(request, *args, **kwargs)
        except Http404:
            # Fallback: check if this is an old slug and redirect to new one
            old_slug = self.kwargs.get("slug", "")
            try:
                import json
                import os
                for redirect_path in ["/data/slug_redirects.json", "/tmp/slug_redirects.json"]:
                    if not os.path.exists(redirect_path):
                        continue
                    with open(redirect_path) as f:
                        redirects = json.load(f)
                    if old_slug in redirects:
                        return redirect(
                            reverse("jobs:job_detail", kwargs={"slug": redirects[old_slug]}),
                            permanent=True,
                        )
            except Exception:
                pass
            raise

    def get_queryset(self):
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        cutoff = now - timedelta(days=180)
        return Job.objects.filter(is_active=True).exclude(
            expires_at__lt=now,
        ).exclude(
            expires_at__isnull=True,
            posted_at__lt=cutoff,
        ).select_related("organization", "category")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Match score for authenticated users with seeker profiles
        context["match_data"] = None
        context["seeker_profile"] = None
        if self.request.user.is_authenticated:
            try:
                seeker = SeekerProfile.objects.get(user=self.request.user)
                if seeker.wizard_completed:
                    context["seeker_profile"] = seeker
                    job = self.object
                    if seeker.embedding is not None and job.embedding is not None:
                        # Get semantic score via cosine distance
                        from pgvector.django import CosineDistance
                        distance = Job.objects.filter(pk=job.pk).annotate(
                            dist=CosineDistance('embedding', seeker.embedding)
                        ).values_list('dist', flat=True).first() or 0
                        semantic_score = (1 - distance) * 100

                        # Get full match using unified service
                        result = UnifiedMatchingService._score_candidate(
                            seeker, job, semantic_score
                        )
                        context["match_data"] = {
                            "total": int(result.score),
                            "breakdown": {
                                "Semantic": int(result.semantic_score),
                                "Profile": int(result.profile_score),
                                "Impact": int(result.impact_score),
                            },
                            "impact_tier": result.impact_tier,
                            "reasons": result.reasons,
                            "impact_reasons": result.impact_reasons,
                        }
            except SeekerProfile.DoesNotExist:
                pass

        # Check if user already applied
        context["has_applied"] = False
        if self.request.user.is_authenticated:
            context["has_applied"] = Application.objects.filter(
                job=self.object, applicant=self.request.user
            ).exists()

        return context


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


class MyMatchesView(LoginRequiredMixin, ListView):
    """Shows top matched jobs for the authenticated seeker."""

    template_name = "jobs/my_matches.html"
    context_object_name = "matches"

    def get_queryset(self):
        return []  # We'll populate in get_context_data

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        try:
            seeker = SeekerProfile.objects.get(user=self.request.user)
            if not seeker.wizard_completed:
                context["needs_profile"] = True
                return context

            context["seeker_profile"] = seeker

            # Unified matching with 4-component scoring
            results = UnifiedMatchingService.get_matches(seeker, limit=25)
            matches = [
                {
                    "job": result.job,
                    "score": int(result.score),
                    "semantic": int(result.semantic_score),
                    "lexical": int(result.lexical_score),
                    "profile": int(result.profile_score),
                    "impact": int(result.impact_score),
                    "impact_tier": result.impact_tier,
                    "reasons": result.reasons,
                    "gaps": result.gaps,
                    "impact_reasons": result.impact_reasons,
                }
                for result in results
            ]
            context["matches"] = matches

        except SeekerProfile.DoesNotExist:
            context["needs_profile"] = True

        return context


class OrganizationSearchView(View):
    """API endpoint for searching all organizations."""

    def get(self, request):
        query = request.GET.get("q", "").strip()
        if len(query) < 2:
            return JsonResponse({"results": []})

        from ..models import Organization

        orgs = Organization.objects.filter(
            jobs__is_active=True,
            name__icontains=query
        ).annotate(
            job_count=Count('jobs', filter=Q(jobs__is_active=True))
        ).order_by('-job_count', 'name').distinct()[:20]

        results = [{"name": org.name, "job_count": org.job_count} for org in orgs]
        return JsonResponse({"results": results})



class CategoryLandingView(ListView):
    """SEO-optimized landing page for each impact category."""
    model = Job
    template_name = "jobs/category_landing.html"
    context_object_name = "jobs"
    paginate_by = 20

    def get_queryset(self):
        self.category = get_object_or_404(Category, slug=self.kwargs["slug"])
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        cutoff = now - timedelta(days=180)
        return (
            Job.objects.filter(is_active=True, category=self.category)
            .exclude(expires_at__lt=now)
            .exclude(expires_at__isnull=True, posted_at__lt=cutoff)
            .select_related("organization", "category")
            .order_by("-posted_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cat = self.category
        context["category"] = cat
        context["categories"] = Category.objects.all().order_by("name")
        context["meta_title"] = f"Remote {cat.name} Jobs \u2014 Remote Impact Jobs"
        context["meta_description"] = (
            f"Browse {context['page_obj'].paginator.count}+ remote {cat.name.lower()} jobs. "
            f"Find purpose-driven roles in {cat.name.lower()} from top impact organizations."
        )
        return context


class AppliedJobDetailView(LoginRequiredMixin, DetailView):
    """Show full job details for a job the user applied to, even if expired."""

    model = Application
    template_name = "jobs/applied_job_detail.html"
    context_object_name = "application"

    def get_queryset(self):
        return Application.objects.filter(
            applicant=self.request.user,
        ).select_related("job", "job__organization", "job__category")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["job"] = self.object.job
        return context
