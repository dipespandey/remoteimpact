from django.views.generic import ListView
from django.shortcuts import get_object_or_404, redirect
from django.db.models import Q, Count
from django.utils import timezone
from datetime import timedelta
from django.http import Http404

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


class OrganizationProfileView(ListView):
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
        
        # Check for SEO-optimized organizations (striking distance keywords)
        seo_data = SEO_OPTIMIZED_ORGS.get(org.slug)
        if seo_data:
            context["meta_title"] = seo_data["meta_title"]
            context["meta_description"] = seo_data["meta_description"].format(count=job_count)
            context["h1_title"] = seo_data["h1_title"]
            context["key_info"] = seo_data.get("key_info", {})
            context["is_seo_optimized"] = True
        else:
            context["meta_title"] = f"Jobs at {org.name} — Remote Impact Jobs"
            context["meta_description"] = (
                f"Browse {job_count} remote job{'' if job_count == 1 else 's'} at {org.name}. "
                f"Find purpose-driven roles at this impact organization."
            )
            context["h1_title"] = org.name
            context["is_seo_optimized"] = False
        
        # Add dateModified for schema freshness signal
        context["date_modified"] = timezone.now().strftime("%Y-%m-%d")
        
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
