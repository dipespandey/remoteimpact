"""Jobs API v1: open remote impact roles, checked daily, as structured JSON.

GET /api/v1/jobs/            filters: q, area, country, remote_scope, job_type, level, min_salary,
                             posted_since (ISO date or Nd), organization, source=employer_posted, page, limit
GET /api/v1/jobs/<slug>/
GET /api/v1/organizations/<slug>/
GET /api/v1/areas/
GET /api/v1/openapi.json
"""
import json
import re
from datetime import timedelta
from functools import wraps

from django.http import JsonResponse, QueryDict
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from jobs.api import serialize
from jobs.api.auth import RateLimited, identify

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, X-Api-Key, Content-Type, Mcp-Protocol-Version, Mcp-Session-Id",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Expose-Headers": "X-RateLimit-Tier, Retry-After, Mcp-Session-Id",
}
ATTRIBUTION = "Data from Remote Impact (remoteimpact.org). Link to the listing URL when you show a role."


def respond(data, status=200, caller=None, cache_seconds=0):
    response = JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False})
    for key, value in CORS.items():
        response[key] = value
    response["Cache-Control"] = f"public, max-age={cache_seconds}" if cache_seconds else "no-store"
    if caller is not None:
        response["X-RateLimit-Tier"] = caller.tier
    return response


def api_view(func):
    @wraps(func)
    @require_http_methods(["GET", "OPTIONS"])
    def wrapper(request, *args, **kwargs):
        if request.method == "OPTIONS":
            return respond({}, status=204)
        try:
            caller = identify(request)
        except RateLimited as exc:
            response = respond({"error": "rate_limited", "message": "Too many requests for this tier.",
                                "docs": serialize._site() + "/developers/"}, status=429)
            response["Retry-After"] = str(exc.retry_after)
            return response
        try:
            return func(request, caller, *args, **kwargs)
        except ValueError as exc:
            return respond({"error": "bad_request", "message": str(exc)}, status=400, caller=caller)
    return wrapper


def _posted_since(value):
    value = (value or "").strip()
    if not value:
        return None
    match = re.fullmatch(r"(\d{1,3})d", value)
    if match:
        return timezone.now() - timedelta(days=int(match.group(1)))
    from django.utils.dateparse import parse_date

    date = parse_date(value)
    if not date:
        raise ValueError("posted_since must be an ISO date (2026-10-01) or a number of days (7d)")
    return timezone.make_aware(timezone.datetime.combine(date, timezone.datetime.min.time()))


def search(params, caller, *, limit=None, page=1):
    """Shared by the REST list endpoint and the MCP search tool. Returns (jobs, total, page, limit)."""
    from jobs.models import Job
    from jobs.services.job_service import JobService

    filters = QueryDict(mutable=True)
    for api_key, internal in (("q", "q"), ("area", "category"), ("job_type", "type"), ("level", "experience"),
                              ("organization", "organization"), ("min_salary", "salary_min")):
        for value in params.getlist(api_key) if hasattr(params, "getlist") else [params.get(api_key)]:
            if value:
                filters.appendlist(internal, str(value))
    if params.get("source") == "employer_posted":
        filters["direct_apply"] = "1"
    text_search = bool(params.get("q"))
    if text_search:
        filters["sort"] = "relevance"  # title matches first, then full-text rank; otherwise newest first
    jobs = JobService.get_filtered_jobs(filters)
    country = params.get("country")
    if country:
        # "Open from this country": roles naming the country, its region (Africa, EMEA...) or worldwide.
        # Per-listing eligibility is in each record (eligible_countries, remote_scope).
        from jobs.api.regions import regions_for

        for value in [country, "Worldwide", *regions_for(country)]:
            filters.appendlist("country", value)
        jobs = JobService.get_filtered_jobs(filters)
    since = _posted_since(params.get("posted_since"))
    if since:
        jobs = jobs.filter(posted_at__gte=since)
    scope = params.get("remote_scope")
    if scope == "worldwide":
        jobs = jobs.filter(location__iregex=r"worldwide|anywhere|global")
    jobs = JobService._collapse_location_variants(jobs)
    if not text_search:
        jobs = jobs.order_by("-posted_at", "-id")
    page_max = caller.limits["page_max"]
    limit = max(1, min(int(limit or params.get("limit") or 20), page_max))
    page = max(1, int(page or params.get("page") or 1))
    total = jobs.count()
    rows = list(jobs.select_related("organization", "category")[(page - 1) * limit: page * limit])
    return rows, total, page, limit


@api_view
def jobs_list(request, caller):
    rows, total, page, limit = search(request.GET, caller)
    return respond({
        "total": total, "page": page, "limit": limit,
        "jobs": [serialize.job_record(job, enriched=caller.limits["enriched"]) for job in rows],
        "attribution": ATTRIBUTION,
    }, caller=caller)


@api_view
def job_detail(request, caller, slug):
    from jobs.models import Job
    from jobs.services.job_service import JobService

    job = JobService.get_available_jobs().select_related("organization", "category").filter(slug=slug).first()
    if job is None and slug.isdigit():
        job = JobService.get_available_jobs().select_related("organization", "category").filter(pk=int(slug)).first()
    if job is None:
        closed = Job.objects.filter(slug=slug).first()
        return respond({"error": "not_found", "message": "No open role with that id.",
                        "status": "closed" if closed else "unknown"}, status=404, caller=caller)
    return respond({"job": serialize.job_record(job, enriched=caller.limits["enriched"]), "attribution": ATTRIBUTION},
                   caller=caller)


@api_view
def organization_detail(request, caller, slug):
    from jobs.models import Organization
    from jobs.services.job_service import JobService

    org = Organization.objects.filter(slug=slug).first()
    if org is None:
        return respond({"error": "not_found"}, status=404, caller=caller)
    roles = JobService.get_available_jobs().filter(organization=org).select_related("organization", "category").order_by("-posted_at")[:50]
    return respond({"organization": serialize.organization_record(org, with_counts=True),
                    "jobs": [serialize.job_record(j, enriched=caller.limits["enriched"]) for j in roles],
                    "attribution": ATTRIBUTION}, caller=caller)


@api_view
def areas(request, caller):
    return respond({"areas": serialize.category_records()}, caller=caller, cache_seconds=3600)


def openapi(request):
    site = serialize._site()
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Remote Impact Jobs API", "version": "1.0",
                 "description": "Open remote roles at impact organizations, checked daily against each employer's own job board. "
                                + ATTRIBUTION + " Docs: " + site + "/developers/"},
        "servers": [{"url": site}],
        "components": {"securitySchemes": {"apiKey": {"type": "http", "scheme": "bearer", "description": "Optional; raises limits."}}},
        "security": [{"apiKey": []}, {}],
        "paths": {
            "/api/v1/jobs/": {"get": {"summary": "Search open roles", "parameters": [
                {"name": n, "in": "query", "schema": {"type": t}, "description": d} for n, t, d in [
                    ("q", "string", "Free-text search (title, organization, keywords)"),
                    ("area", "string", "Impact area slug, e.g. climate-environment, ai-safety, global-health (see /api/v1/areas/)"),
                    ("country", "string", "Country the applicant lives in; keeps roles open to it"),
                    ("remote_scope", "string", "worldwide to keep only roles open anywhere"),
                    ("job_type", "string", "full-time, part-time, contract, freelance, internship"),
                    ("level", "string", "internship, entry, mid, senior, executive"),
                    ("min_salary", "integer", "Minimum annual pay"),
                    ("posted_since", "string", "ISO date or Nd, e.g. 7d"),
                    ("organization", "string", "Organization name contains"),
                    ("source", "string", "employer_posted to keep only roles posted directly on Remote Impact"),
                    ("page", "integer", "Page number"), ("limit", "integer", "Results per page (max depends on tier)")]],
                "responses": {"200": {"description": "Jobs"}, "429": {"description": "Rate limited"}}}},
            "/api/v1/jobs/{slug}/": {"get": {"summary": "One role by slug or id",
                                             "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                                             "responses": {"200": {"description": "Job"}, "404": {"description": "Closed or unknown"}}}},
            "/api/v1/organizations/{slug}/": {"get": {"summary": "An organization and its open roles",
                                                      "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                                                      "responses": {"200": {"description": "Organization"}}}},
            "/api/v1/areas/": {"get": {"summary": "Impact areas with open-role counts", "responses": {"200": {"description": "Areas"}}}},
        },
    }
    return respond(spec, cache_seconds=3600)
