"""Small, source-linked observations of our catalogue, not market estimates."""
import json
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView
from django.contrib.sitemaps import Sitemap

from jobs.services.job_service import JobService


TOPICS = {
    "climate-hiring": ("Remote climate hiring snapshot", "climate-environment"),
    "ai-safety-hiring": ("Remote AI safety hiring snapshot", "ai-safety"),
    "global-health-hiring": ("Remote global health hiring snapshot", "global-health"),
    "early-career-hiring": ("Entry-level remote impact hiring snapshot", None),
}


class HiringEvidenceSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.7

    def items(self):
        return [*TOPICS, "nonprofit-hiring-locations"]

    def location(self, item):
        if item == "nonprofit-hiring-locations":
            return reverse("jobs:nonprofit_hiring_locations")
        return reverse("jobs:hiring_evidence", kwargs={"topic": item})


class HiringEvidenceView(TemplateView):
    template_name = "jobs/hiring_evidence.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        topic = TOPICS.get(kwargs["topic"])
        if topic is None:
            raise Http404
        title, category = topic
        jobs = JobService.get_available_jobs()
        if category:
            jobs = jobs.filter(category__slug=category)
        else:
            jobs = jobs.filter(experience_level__in=["entry", "internship"])
        now = timezone.now()
        counts = jobs.aggregate(
            total=Count("pk"), employers=Count("organization_id", distinct=True),
            recent=Count("pk", filter=Q(posted_at__gte=now-timedelta(days=7), posted_at__lte=now)),
            salary=Count("pk", filter=Q(salary_min__gt=0) | Q(salary_max__gt=0)),
        )
        def breakdown(field):
            return list(jobs.exclude(**{field: ""}).exclude(**{f"{field}__isnull": True})
                        .values(field).annotate(total=Count("pk")).order_by("-total", field)[:10])
        countries = [{"label": row["country"], "total": row["total"]} for row in breakdown("country")]
        from jobs.models import Job
        levels = dict(Job._meta.get_field("experience_level").choices)
        experience = [{"label": levels.get(row["experience_level"], row["experience_level"]), "total": row["total"]}
                      for row in breakdown("experience_level")]
        rows = list(jobs.select_related("organization").order_by("-posted_at", "-pk")[:12])
        url = settings.SITE_URL.rstrip("/") + reverse("jobs:hiring_evidence", kwargs={"topic": kwargs["topic"]})
        schema = {
            "@context": "https://schema.org", "@type": "Dataset", "name": title,
            "url": url, "description": "Current Remote Impact catalogue observations, not a census of the employment market or verified applicant eligibility.",
            "creator": {"@type": "Organization", "name": "Remote Impact", "url": settings.SITE_URL},
            "dateModified": now.isoformat(), "temporalCoverage": now.date().isoformat(),
            "isAccessibleForFree": True,
        }
        context.update(title=title, counts=counts, observed_at=now, countries=countries,
                       experience=experience, evidence_jobs=rows, schema_json=json.dumps(schema),
                       topics=[{"slug": key, "title": value[0]} for key, value in TOPICS.items()],
                       salary_percent=round(100*counts["salary"]/counts["total"]) if counts["total"] else 0)
        return context
