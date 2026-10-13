"""Job, organization and category records for the API and the MCP server.

Listings aggregated from other boards keep only an excerpt of the description plus a link; full text is
served for roles employers posted on Remote Impact. Everything else (eligibility, pay, verification,
must-haves, organization data) is our own work and is what the API is for.
"""
import re

from django.conf import settings
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.html import strip_tags

EXCERPT_FREE = 300
EXCERPT_COMMERCIAL = 600
GLOBAL = re.compile(r"\b(?:worldwide|anywhere|global(?:ly)?|international)\b", re.I)
REGION_LABELS = {"europe", "eu", "emea", "africa", "asia", "apac", "latam", "latin america", "americas", "middle east", "mena",
                 "north america", "oceania", "asia pacific", "sub-saharan africa"}


def _site():
    return getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")


def _text(value, limit):
    text = " ".join(strip_tags(value or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def eligible_countries(job):
    from jobs.services.job_quality import label_applicant_countries, schema_applicant_countries

    return schema_applicant_countries(job) or label_applicant_countries(job)


def verification(job):
    raw = job.raw_data or {}
    check = raw.get("source_verification") if isinstance(raw.get("source_verification"), dict) else {}
    if check:
        return {
            "status": check.get("status") or "listed",
            "method": "employer_job_board" if check.get("method") == "ats_api" else "source_feed",
            "checked_at": check.get("checked_at"),
        }
    return {"status": "listed", "method": "employer_posted" if job.source == "manual" else "source_feed",
            "checked_at": job.last_seen_at.isoformat() if job.last_seen_at else None}


def salary(job):
    if not (job.salary_min or job.salary_max):
        return None
    raw = job.raw_data or {}
    period = str(raw.get("salary_period") or raw.get("salaryPeriod") or "year").lower()
    period = {"annual": "year", "annually": "year", "yearly": "year", "hr": "hour", "hourly": "hour", "monthly": "month"}.get(period, period)
    return {"min": float(job.salary_min) if job.salary_min else None, "max": float(job.salary_max) if job.salary_max else None,
            "currency": job.salary_currency or "USD", "period": period}


def organization_record(org, *, with_counts=False):
    from jobs.services.outbound_links import with_outbound_attribution
    site = _site()
    record = {
        "name": org.name, "slug": org.slug,
        "url": site + reverse("jobs:organization_profile", kwargs={"slug": org.slug}) if org.slug else None,
        "homepage_url": with_outbound_attribution(org.homepage_url or org.website) or None,
        "careers_url": with_outbound_attribution(org.careers_url) or None,
        "logo_url": (site + org.logo_src) if (org.logo_src and org.logo_src.startswith("/")) else (org.logo_src or None),
    }
    if with_counts:
        from jobs.services.job_service import JobService

        record["open_roles"] = JobService.get_available_jobs().filter(organization=org).count()
    return record


def job_record(job, *, enriched=False):
    site = _site()
    countries = eligible_countries(job)
    location = job.location or ""
    listed = (job.country or "").strip()  # normalized label from the listing: "USA", "Worldwide", "Europe"...
    if countries:
        scope = "countries"
    elif GLOBAL.search(location) or GLOBAL.search(listed):
        scope = "worldwide"
    elif listed and listed.lower() in REGION_LABELS:
        scope = "region"
    else:
        scope = "unspecified"
    listing_url = site + job.get_absolute_url()
    apply_url = (site + reverse("jobs:job_apply_redirect", kwargs={"slug": job.slug}) if job.application_url
                 else (site + reverse("jobs:apply_job", kwargs={"slug": job.slug}) if job.source == "manual" else listing_url))
    record = {
        "id": job.pk, "slug": job.slug, "title": job.title,
        "organization": organization_record(job.organization),
        "area": {"name": job.category.name, "slug": job.category.slug} if job.category else None,
        "location": location or "Remote", "remote_scope": scope, "eligible_countries": countries,
        "listed_country": listed or None,
        "job_type": job.job_type, "experience_level": job.experience_level or None,
        "salary": salary(job),
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "content_updated_at": job.edited_at.isoformat() if job.edited_at else None,
        "expires_at": job.expires_at.isoformat() if job.expires_at else None,
        "verification": verification(job),
        "source": "employer_posted" if job.source == "manual" else "aggregated",
        "featured": bool(job.is_sponsored),
        "url": listing_url, "apply_url": apply_url,
        "description_excerpt": _text(job.description, EXCERPT_COMMERCIAL if enriched else EXCERPT_FREE),
    }
    if enriched:
        record.update({
            "education_level": job.education_level or None,
            "skills": list(job.skills or []),
            "must_haves": [{"label": m.get("label"), "quote": m.get("quote")} for m in (job.must_haves or [])],
            "requirements_excerpt": _text(job.requirements, EXCERPT_COMMERCIAL),
            "description": " ".join(strip_tags(job.description or "").split()) if job.source == "manual" else None,
        })
    return record


def category_records():
    from django.db.models import Count

    from jobs.models import Category
    from jobs.services.job_service import JobService

    counts = dict(JobService.get_available_jobs().values_list("category__slug").annotate(n=Count("id")))
    return [{"name": c.name, "slug": c.slug, "open_roles": counts.get(c.slug, 0),
             "url": _site() + reverse("jobs:category_landing", kwargs={"slug": c.slug})}
            for c in Category.objects.order_by("name") if counts.get(c.slug)]
