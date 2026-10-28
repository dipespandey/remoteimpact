from django.views.generic import ListView
from django.http import Http404
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta
from django.utils.decorators import method_decorator
from ..services.page_cache import cache_public_page
from .mixins import FastJobPaginationMixin

from ..models import Job, Category
from ..seo_config import MIN_INDEXABLE_JOBS


REGIONS = {
    "worldwide": {
        "name": "Worldwide",
        "h1": "Remote Jobs Around the World",
        "description": (
            "Browse all remote jobs from impact-driven organizations around the globe. "
            "Hiring countries, office attendance and travel requirements vary by role; check the employer’s posting before applying."
        ),
        "filter": None,  # No location filter — show all
    },
    "united-states": {
        "name": "United States",
        "h1": "Remote Jobs in the United States",
        "description": (
            "Find remote roles based in or open to candidates in the United States. "
            "Nonprofits and social enterprises hiring people based in the US."
        ),
        "filter": [
            "United States", "USA", "US", "U.S.", "U.S.A.",
            "New York", "California", "Texas", "Florida", "Washington",
            "Oregon", "Colorado", "Massachusetts", "Illinois", "Georgia",
        ],
    },
    "europe": {
        "name": "Europe",
        "h1": "Remote Jobs in Europe",
        "description": (
            "Discover remote opportunities across Europe. Nonprofits and impact-focused companies "
            "hiring talent in the UK, Germany, France, Netherlands, and beyond."
        ),
        "filter": [
            "Europe", "UK", "United Kingdom", "England", "Germany", "France",
            "Netherlands", "Spain", "Italy", "Sweden", "Denmark", "Norway",
            "Finland", "Switzerland", "Austria", "Belgium", "Ireland",
            "Portugal", "Poland", "Czech", "Romania", "Hungary", "Greece",
            "Luxembourg", "Estonia", "Latvia", "Lithuania", "Croatia",
            "Slovakia", "Slovenia", "Bulgaria", "EMEA",
        ],
    },
    "africa": {
        "name": "Africa",
        "h1": "Remote Jobs in Africa",
        "description": (
            "Remote roles open to candidates across Africa. Impact organizations hiring "
            "in Kenya, Nigeria, South Africa, Ghana, Ethiopia, and more."
        ),
        "filter": [
            "Africa", "Kenya", "Nigeria", "South Africa", "Ghana", "Ethiopia",
            "Tanzania", "Uganda", "Rwanda", "Senegal", "Cameroon", "Egypt",
            "Morocco", "Tunisia", "Mozambique", "Zimbabwe", "Zambia", "Malawi",
            "Mali", "Niger", "Somalia", "Sudan", "Congo", "Ivory Coast",
            "Madagascar", "Angola", "Botswana", "Namibia", "SSA",
            "Sub-Saharan Africa",
        ],
    },
    "asia": {
        "name": "Asia",
        "h1": "Remote Jobs in Asia",
        "description": (
            "Find remote positions open to talent in Asia. Organizations making an impact "
            "in India, Philippines, Japan, Singapore, and the broader region."
        ),
        "filter": [
            "Asia", "India", "Philippines", "Japan", "Singapore", "China",
            "South Korea", "Thailand", "Vietnam", "Indonesia", "Malaysia",
            "Bangladesh", "Pakistan", "Sri Lanka", "Nepal", "Myanmar",
            "Cambodia", "Laos", "Mongolia", "Taiwan", "Hong Kong",
            "APAC", "Asia-Pacific",
        ],
    },
    "latin-america": {
        "name": "Latin America",
        "h1": "Remote Jobs in Latin America",
        "description": (
            "Remote opportunities for candidates in Latin America. Impact-focused roles "
            "in Brazil, Mexico, Colombia, Argentina, and across the region."
        ),
        "filter": [
            "Latin America", "LATAM", "Brazil", "Mexico", "Colombia",
            "Argentina", "Chile", "Peru", "Ecuador", "Venezuela", "Bolivia",
            "Paraguay", "Uruguay", "Costa Rica", "Panama", "Guatemala",
            "Honduras", "El Salvador", "Nicaragua", "Dominican Republic",
            "Cuba", "Puerto Rico", "Caribbean",
        ],
    },
}


def region_jobs(region):
    """Publishable jobs for a region (same rule as the job list, sitemap and robots tag)."""
    qs = Job.objects.filter(Job.availability_filter())
    location_terms = region.get("filter")
    if location_terms:
        q = Q()
        for term in location_terms:
            q |= Q(location__icontains=term) | Q(country__icontains=term)
        qs = qs.filter(q)
    return qs


@method_decorator(cache_public_page(120), name="dispatch")
class RegionJobsView(FastJobPaginationMixin, ListView):
    """SEO-optimized landing page for a geographic region."""
    model = Job
    template_name = "jobs/region_landing.html"
    context_object_name = "jobs"
    paginate_by = 20

    def get_queryset(self):
        slug = self.kwargs["region_slug"]
        if slug not in REGIONS:
            raise Http404
        self.region = REGIONS[slug]
        self.region_slug = slug

        qs = region_jobs(self.region).select_related("organization", "category")

        # Optional category filter
        cat_slug = self.request.GET.get("category")
        if cat_slug:
            qs = qs.filter(category__slug=cat_slug)

        return qs.order_by("-posted_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["region"] = self.region
        context["region_slug"] = self.region_slug
        context["regions"] = REGIONS
        context["categories"] = Category.objects.all().order_by("name")
        context["current_category"] = self.request.GET.get("category", "")
        job_count = context["page_obj"].paginator.count
        context["index_page"] = job_count >= MIN_INDEXABLE_JOBS and not self.request.GET
        context["meta_title"] = f"{self.region['h1']} — Remote Impact Jobs"
        context["meta_description"] = (
            f"Browse {job_count} remote impact jobs across countries. Check each employer’s hiring location and working arrangements."
            if self.region_slug == "worldwide" else
            f"Browse {job_count}+ remote jobs in {self.region['name']}. "
            f"Find purpose-driven roles from top impact organizations."
        )
        return context
