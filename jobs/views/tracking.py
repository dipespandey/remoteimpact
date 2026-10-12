"""Lightweight engagement tracking that employers can be shown."""
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from ..models import Job
from ..services import job_stats
from ..services.job_quality import safe_application_destination


@method_decorator(csrf_exempt, name="dispatch")
class JobViewBeaconView(View):
    """Counted from a small script after the page is visible; bots rarely run it."""

    def post(self, request, slug):
        job_id = Job.objects.filter(slug=slug).values_list("pk", flat=True).first()
        if job_id and not job_stats.is_probably_bot(request):
            job_stats.record(job_id, "views", request=request)
        return HttpResponse(status=204)


class JobApplyRedirectView(View):
    """Outbound apply link: counts the click, then sends the person to the employer."""

    def get(self, request, slug):
        job = get_object_or_404(Job.objects.only("pk", "slug", "application_url"), slug=slug)
        destination = safe_application_destination(job.application_url)
        if not destination:
            raise Http404("This job has no external application link.")
        if not job_stats.is_probably_bot(request) and job_stats.viewed_today(job.pk, request):
            job_stats.record(job.pk, "apply_clicks", request=request)
        response = HttpResponseRedirect(job_stats.with_referral(destination))
        response["X-Robots-Tag"] = "noindex, nofollow"
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "origin"
        return response


def organization_logo(request, slug):
    """An organization's fetched logo, served from our own domain (the URL carries a version)."""
    from django.http import HttpResponse

    from ..models import OrganizationLogo

    logo = OrganizationLogo.objects.only("png").filter(organization__slug=slug).first()
    if logo is None:
        return HttpResponse(status=404)
    response = HttpResponse(bytes(logo.png), content_type="image/png")
    response["Cache-Control"] = "public, max-age=2592000, immutable"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def jobs_json_feed(request):
    """Open roles as JSON Feed 1.1, for AI assistants, aggregators and partner sites."""
    from django.conf import settings
    from django.http import JsonResponse
    from django.utils.html import strip_tags

    from ..services.job_service import JobService

    site = settings.SITE_URL.rstrip("/")
    jobs = (JobService._collapse_location_variants(JobService.get_available_jobs())
            .select_related("organization", "category").order_by("-posted_at")[:200])
    items = []
    for job in jobs:
        url = site + job.get_absolute_url()
        items.append({
            "id": url, "url": url, "title": f"{job.title} at {job.organization.name}",
            "summary": " ".join(strip_tags(job.description or "").split())[:300],
            "date_published": job.posted_at.isoformat() if job.posted_at else None,
            "tags": [t for t in (job.category.name if job.category else "", job.location, job.job_type) if t],
            "_remote_impact": {
                "organization": job.organization.name, "organization_homepage": job.organization.homepage_url or None,
                "location": job.location, "country": job.country, "job_type": job.job_type,
                "salary_min": float(job.salary_min) if job.salary_min else None,
                "salary_max": float(job.salary_max) if job.salary_max else None,
                "salary_currency": job.salary_currency or None,
            },
        })
    response = JsonResponse({
        "version": "https://jsonfeed.org/version/1.1",
        "title": "Remote Impact: latest remote jobs at impact organizations",
        "home_page_url": site + "/jobs/", "feed_url": site + "/feed/jobs.json",
        "description": "Remote roles in climate, global health, AI safety and social impact, checked daily against each employer's own job board.",
        "items": items,
    }, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "public, max-age=900"
    return response
