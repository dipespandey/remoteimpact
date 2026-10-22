from django.views.generic import ListView
from django.shortcuts import get_object_or_404, redirect
from django.db.models import Q, Count, Max
from django.utils.decorators import method_decorator
from ..services.page_cache import cache_public_page
from .mixins import FastJobPaginationMixin

from ..models import Job, Organization, Category

# Map old broken slugs to new correct slugs for 301 redirects
OLD_SLUG_REDIRECTS = {
    "fort20health": "fort-health",
    "fort20health-1": "fort-health",
    "grindr20llc": "grindr-llc",
    "grindr20llc-1": "grindr-llc",
    "nautilus20biotechnology": "nautilus-biotechnology",
    "new20story": "new-story",
    "solana20foundation": "solana-foundation",
    "solana20foundation-1": "solana-foundation",
    "the20agency20fund": "the-agency-fund",
    "the20agency20fund-1": "the-agency-fund",
    "djurens-ratt-1": "djurens-ratt",
    "farai-1": "farai",
}


@method_decorator(cache_public_page(120), name="dispatch")
class OrganizationProfileView(FastJobPaginationMixin, ListView):
    """SEO-optimized profile page for an organization."""
    model = Job
    template_name = "jobs/organization_profile.html"
    context_object_name = "jobs"
    paginate_by = 20

    def get(self, request, *args, **kwargs):
        slug = self.kwargs.get("slug")
        # Check if this is an old slug that needs redirecting
        if slug in OLD_SLUG_REDIRECTS:
            new_slug = OLD_SLUG_REDIRECTS[slug]
            return redirect("jobs:organization_profile", slug=new_slug, permanent=True)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        self.organization = get_object_or_404(Organization, slug=self.kwargs["slug"])
        # The same publishable set as the job list and the sitemap, so the page, its robots tag and
        # the sitemap always agree on whether this employer has open roles.
        return (
            Job.objects.filter(Job.availability_filter(), organization=self.organization)
            .select_related("organization", "category")
            .order_by("-posted_at")
        )

    @staticmethod
    def _hiring_summary(org, jobs):
        """Describe recorded available listings without inferring applicant eligibility."""
        from collections import Counter
        rows = list(jobs.values_list("category__name", "job_type", "location", "salary_min", "salary_max", "salary_currency", "raw_data"))
        if not rows:
            return {}
        areas = [a for a, _ in Counter(r[0] for r in rows if r[0]).most_common(3)]
        types = [t.replace("-", " ") for t, _ in Counter(r[1] for r in rows if r[1]).most_common(2)]
        global_labels = sum(1 for r in rows if (r[2] or "").strip().lower() in {
            "global", "worldwide", "remote, global", "remote (global)",
            "remote, worldwide", "remote (worldwide)",
        })
        annual = {"year", "yearly", "annual", "annually"}
        annual_bounds = []
        for row in rows:
            raw = row[6] if isinstance(row[6], dict) else {}
            periods = [str(raw[key]).strip().lower() for key in ("salaryPeriod", "salary_period")
                       if raw.get(key) is not None and str(raw[key]).strip()]
            if (row[5] or "").upper() != "USD" or not periods or any(p not in annual for p in periods):
                continue
            low, high = row[3], row[4]
            if (low is not None and low <= 0) or (high is not None and high <= 0):
                continue
            if low is not None and high is not None and low > high:
                continue
            bounds = [value for value in (low, high) if value is not None]
            if bounds:
                annual_bounds.append(bounds)
        summary = {"open": len(rows), "areas": areas, "types": types, "global_labels": global_labels}
        if len(annual_bounds) >= 3:
            summary["pay_low"] = min(min(bounds) for bounds in annual_bounds)
            summary["pay_high"] = max(max(bounds) for bounds in annual_bounds)
            summary["pay_count"] = len(annual_bounds)
        return summary

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        org = self.organization
        context["organization"] = org
        context["categories"] = (
            Category.objects.filter(
                jobs__in=self.object_list,
            ).distinct().order_by("name")
        )
        job_count = context["page_obj"].paginator.count
        context["job_count"] = job_count
        context["index_page"] = job_count > 0 and not self.request.GET
        
        context["hiring_summary"] = self._hiring_summary(org, context["page_obj"].paginator.object_list)
        # Describe the current inventory, without promising unverified locations,
        # internship cycles, eligibility or a daily source-verification cadence.
        roles = f"{job_count} open remote role{'' if job_count == 1 else 's'}"
        context["meta_title"] = f"{org.name} careers: {roles}" if job_count else f"{org.name} careers and jobs"
        areas = ", ".join(context["hiring_summary"].get("areas", [])[:2])
        context["meta_description"] = (
            f"Browse {roles} at {org.name}{' in ' + areas if areas else ''} on Remote Impact. "
            "See listing details, location requirements and how to apply."
            if job_count else f"{org.name} has no open remote roles on Remote Impact right now. See similar impact jobs."
        )
        context["h1_title"] = org.name
        context["is_seo_optimized"] = False
        # Listing edits, including closures, are evidence of an inventory change.
        # Request time is not a content modification date.
        latest_change = Job.objects.filter(organization=org).aggregate(latest=Max("updated_at"))["latest"]
        context["date_modified"] = latest_change.isoformat() if latest_change else None

        return context


@method_decorator(cache_public_page(60), name="dispatch")
class OrganizationDirectoryView(ListView):
    """Directory of all organizations with active jobs."""
    model = Organization
    template_name = "jobs/organization_directory.html"
    context_object_name = "organizations"
    paginate_by = 30

    def get_queryset(self):
        # Match profile pages and the sitemap, including freshness, quality and
        # rejected-employer rules. An expired sibling must not hide an open job.
        visible_jobs = Job.objects.filter(Job.availability_filter())
        qs = (
            Organization.objects.exclude(slug__in=OLD_SLUG_REDIRECTS)
            .annotate(
                active_job_count=Count("jobs", filter=Q(jobs__in=visible_jobs))
            )
            .filter(active_job_count__gt=0)
            .order_by("-active_job_count", "name")
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
            "Browse nonprofits and impact-focused companies hiring for remote roles. "
            "Find nonprofits, social enterprises, and B Corps with open positions."
        )
        return context
