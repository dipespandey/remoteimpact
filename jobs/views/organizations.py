from django.views.generic import ListView
from django.shortcuts import get_object_or_404
from django.db.models import Q, Count
from django.utils import timezone
from datetime import timedelta

from ..models import Job, Organization, Category


class OrganizationProfileView(ListView):
    """SEO-optimized profile page for an organization."""
    model = Job
    template_name = "jobs/organization_profile.html"
    context_object_name = "jobs"
    paginate_by = 20

    def get_queryset(self):
        self.organization = get_object_or_404(Organization, slug=self.kwargs["slug"])
        now = timezone.now()
        cutoff = now - timedelta(days=180)
        return (
            Job.objects.filter(is_active=True, organization=self.organization)
            .exclude(expires_at__lt=now)
            .exclude(expires_at__isnull=True, posted_at__lt=cutoff)
            .select_related("organization", "category")
            .order_by("-posted_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        org = self.organization
        context["organization"] = org
        context["categories"] = (
            Category.objects.filter(
                jobs__organization=org,
                jobs__is_active=True,
            ).distinct().order_by("name")
        )
        job_count = context["page_obj"].paginator.count
        context["job_count"] = job_count
        context["meta_title"] = f"Jobs at {org.name} — Remote Impact Jobs"
        context["meta_description"] = (
            f"Browse {job_count} remote job{'' if job_count == 1 else 's'} at {org.name}. "
            f"Find purpose-driven roles at this impact organization."
        )
        return context


class OrganizationDirectoryView(ListView):
    """Directory of all organizations with active jobs."""
    model = Organization
    template_name = "jobs/organization_directory.html"
    context_object_name = "organizations"
    paginate_by = 30

    def get_queryset(self):
        now = timezone.now()
        cutoff = now - timedelta(days=180)
        qs = (
            Organization.objects.filter(
                jobs__is_active=True,
            )
            .exclude(jobs__expires_at__lt=now)
            .annotate(
                active_job_count=Count(
                    "jobs",
                    filter=Q(
                        jobs__is_active=True,
                    ) & ~Q(jobs__expires_at__lt=now),
                )
            )
            .filter(active_job_count__gt=0)
            .order_by("-active_job_count", "name")
            .distinct()
        )
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(name__icontains=q)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_query"] = self.request.GET.get("q", "")
        context["meta_title"] = "Impact Organizations Hiring Remotely — Remote Impact Jobs"
        context["meta_description"] = (
            "Browse purpose-driven organizations hiring for remote roles. "
            "Find nonprofits, social enterprises, and B Corps with open positions."
        )
        return context
