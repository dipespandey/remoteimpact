from django.views.generic import ListView
from django.shortcuts import get_object_or_404, redirect
from django.db.models import Q, Count
from django.utils import timezone
from django.utils.decorators import method_decorator
from ..services.page_cache import cache_public_page
from .mixins import FastJobPaginationMixin

from ..models import Job, Organization, Category

# SEO-optimized metadata for "striking distance" keywords
# These organizations have specific high-value search queries we're targeting
SEO_OPTIMIZED_ORGS = {
    "carnegie-endowment-for-international-peace": {
        "meta_title": "Carnegie Endowment for International Peace Internships & Jobs 2026 | Remote Impact",
        "meta_description": "Apply now for Carnegie Endowment for International Peace internships and jobs in 2026. Browse {count} open roles in policy research, communications, and international affairs. Remote & DC-based positions available.",
        "h1_title": "Carnegie Endowment for International Peace Internships & Jobs",
        "key_info": {
            "organization_type": "Think Tank / Policy Research",
            "headquarters": "Washington, DC",
            "focus_areas": "International Affairs, Foreign Policy, Nuclear Policy, Democracy",
            "internship_cycles": "Spring, Summer, Fall (Rolling applications)",
            "eligibility": "Graduate students, Recent graduates, Early-career professionals",
        }
    },
    "chatham-house": {
        "meta_title": "Chatham House Jobs & Careers 2026 | Remote Impact",
        "meta_description": "Apply now for Chatham House jobs in 2026. Browse {count} open roles at the Royal Institute of International Affairs. Research, policy, and communications positions in London & remote.",
        "h1_title": "Chatham House Jobs & Careers",
        "key_info": {
            "organization_type": "Think Tank / Policy Research",
            "headquarters": "London, UK",
            "focus_areas": "International Affairs, Geopolitics, Energy, Economy",
            "also_known_as": "Royal Institute of International Affairs",
            "eligibility": "Researchers, Policy analysts, Communications professionals",
        }
    },
}

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
        """Facts about what this employer is hiring for, from its live listings (empty when too little)."""
        from collections import Counter
        rows = list(jobs.values_list("category__name", "job_type", "location", "salary_min", "salary_max", "salary_currency"))
        if not rows:
            return {}
        areas = [a for a, _ in Counter(r[0] for r in rows if r[0]).most_common(3)]
        types = [t.replace("-", " ") for t, _ in Counter(r[1] for r in rows if r[1]).most_common(2)]
        worldwide = sum(1 for r in rows if (r[2] or "").strip().lower() in {"remote", "remote, global", "remote (global)"})
        mids = [(float(r[3] or r[4]) + float(r[4] or r[3])) / 2 for r in rows if r[5] == "USD" and (r[3] or r[4])]
        summary = {"open": len(rows), "areas": areas, "types": types, "worldwide": worldwide}
        if len(mids) >= 3:
            summary["pay_low"], summary["pay_high"] = round(min(mids), -3), round(max(mids), -3)
            summary["pay_count"] = len(mids)
        return summary

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
        context["index_page"] = job_count > 0 and not self.request.GET
        
        context["hiring_summary"] = self._hiring_summary(org, context["page_obj"].paginator.object_list)
        # Check for SEO-optimized organizations (striking distance keywords)
        seo_data = SEO_OPTIMIZED_ORGS.get(org.slug)
        if seo_data:
            context["meta_title"] = seo_data["meta_title"]
            context["meta_description"] = seo_data["meta_description"].format(count=job_count)
            context["h1_title"] = seo_data["h1_title"]
            context["key_info"] = seo_data.get("key_info", {})
            context["is_seo_optimized"] = True
        else:
            # People search "<employer> careers" or "<employer> jobs"; lead with the name and that word.
            roles = f"{job_count} open remote role{'' if job_count == 1 else 's'}"
            context["meta_title"] = f"{org.name} careers: {roles}" if job_count else f"{org.name} careers and jobs"
            areas = ", ".join((context.get("hiring_summary") or {}).get("areas", [])[:2])
            context["meta_description"] = (
                f"{org.name} is hiring: {roles}{' in ' + areas if areas else ''}, each checked daily against "
                f"{'their own job board' if org.careers_url else 'the employer’s site'}. See pay, location and how to apply."
                if job_count else f"{org.name} has no open remote roles on Remote Impact right now. See similar impact jobs."
            )
            context["h1_title"] = org.name
            context["is_seo_optimized"] = False
        
        # Add dateModified for schema freshness signal
        context["date_modified"] = timezone.now().strftime("%Y-%m-%d")
        
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
