from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from copy import deepcopy
from datetime import datetime, timezone as dt_timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from asgiref.sync import sync_to_async
from django.db import connections, transaction
from django.db.models import Q
from django.utils import timezone

from jobs.models import Category, Job, Organization, JobSlugRedirect
from jobs.utils import unique_slug
from jobs.constants import IMPACT_AREAS
from jobs.services.location_normalizer import normalize_location
from jobs.services.job_enrichment import enrich_job_payload
from jobs.services.job_quality import refresh_job_quality

logger = logging.getLogger(__name__)
UNIQUE_ATS_POSTING_PREFIXES = ("ashby:", "lever:", "greenhouse:", "workable:", "personio:",
                               "smartrecruiters:", "breezy:", "idealist:")

# Build lookup for standard impact areas
_IMPACT_AREA_BY_SLUG = {area["slug"]: area for area in IMPACT_AREAS}

_GENERIC_LISTING_TITLES = {
    "expression of interest",
    "general application",
    "general applications",
    "internship",
    "internships",
    "open application",
    "volunteer",
}

_NON_OPENING_LISTING_PATTERNS = (
    r"\b(?:talent community|talent pool|candidate pool)\b",
    r"\b(?:future talent|future role|future openings?)\b",
    r"\b(?:general interest|general applicant|general application|general applications)\b",
    r"\b(?:open application|open applications|unsolicited application|unsolicited applications)\b",
    r"\b(?:speculative application|proactive application|initiative application)\b",
    r"\b(?:join our team|interested in joining(?: our team)?|your \(role\) here)\b",
    r"\b(?:your )?\(?role\)? here\b",
    r"\b(?:don(?:'?| t )see (?:the )?role|submit your (?:cv|resume))\b",
    r"\b(?:intrigued\?? apply here|general submission|general role)\b",
    r"\b(?:list of events|career development and transition funding|exceptional talent application)\b",
    r"^volunteers?$",
    r"\b(?:submit your resume|drop your resume|cv database)\b",
    r"\b(?:future opportunit(?:y|ies)|expression of interest)\b",
    r"\b(?:don(?:'?| t )see (?:the )?right role|stay in touch)\b",
    r"^any job$",
    r"^get career (?:advising|advice)$",
    r"\binitiativbewerbung\b",
    r"\bcandidatur[ae] spontan",
    r"\btalent network\b",
    r"^list of (?:internship|places|challenges|programs?|organi[sz]ations|courses|events|fellowships)\b",
    r"\bcan t find (?:the |a )?right role\b",
)

PUBLIC_DESCRIPTION_MIN_LENGTH = 120
# A real advertised range is rarely more than ~3x wide; wider ones are mixed currencies
# ("£36k ... 5.1M KES") or ATS placeholder bands ("$10,000 - $600,000").
MAX_SALARY_RANGE_RATIO = 5


def drop_implausible_salary(defaults: Dict[str, Any]) -> None:
    low, high = defaults.get("salary_min"), defaults.get("salary_max")
    if low and high and float(low) > 0 and float(high) / float(low) > MAX_SALARY_RANGE_RATIO:
        raw = dict(defaults.get("raw_data") or {})
        raw["salary_rejected"] = {"min": str(low), "max": str(high), "currency": defaults.get("salary_currency") or "",
                                  "reason": "range_too_wide"}
        defaults["raw_data"] = raw
        defaults["salary_min"] = defaults["salary_max"] = None
        defaults["salary_currency"] = ""


def _public_listing_quality_ready(payload: Dict[str, Any]) -> bool:
    """Return whether a newly imported external record is safe to publish."""
    if payload.get("source") == Job.Source.MANUAL:
        return True
    description = " ".join(str(payload.get("description") or "").split())
    has_destination = bool(
        str(payload.get("application_url") or "").strip()
        or str(payload.get("application_email") or "").strip()
    )
    # Job-board search results are intentionally persisted as inactive
    # placeholders so the detail crawler can repair them before publication.
    needs_crawling = bool((payload.get("raw_data") or {}).get("needs_crawling"))
    return (
        len(description) >= PUBLIC_DESCRIPTION_MIN_LENGTH
        and has_destination
        and not needs_crawling
    )


def _normalize_duplicate_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).lower()


def _normalize_duplicate_url(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).rstrip("/").lower()


def _sanitize_public_url(value: Any) -> str:
    """Return a safe absolute web URL for public organization links."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or any(character in value for character in ("{", "}", "'", '"')):
        return ""
    if value.startswith("//"):
        value = f"https:{value}"
    elif not re.match(r"^https?://", value, flags=re.IGNORECASE):
        if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(?:[/:?#].*)?$", value, flags=re.IGNORECASE):
            value = f"https://{value}"
        else:
            return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    if any(character.isspace() for character in parsed.netloc):
        return ""
    return value


def _normalize_duplicate_title(value: Any) -> str:
    title = _normalize_duplicate_text(value)
    title = re.sub(
        r"\s*[-–—|]\s*(remote|hybrid|full[- ]time|part[- ]time)\s*$",
        "",
        title,
    )
    title = re.sub(r"[^a-z0-9+#.]+", " ", title)
    return _normalize_duplicate_text(title)


def _is_generic_listing_title(value: Any) -> bool:
    """Reject talent pools and generic forms that are not concrete openings."""
    from jobs.services.scout_verification import non_opening
    if non_opening(value):
        return True
    title = _normalize_duplicate_title(value)
    return title in _GENERIC_LISTING_TITLES or any(
        re.search(pattern, title, flags=re.IGNORECASE)
        for pattern in _NON_OPENING_LISTING_PATTERNS
    )


def _canonical_duplicate_url(value: Any) -> str:
    """Identify a source posting, preserving functional IDs on generic URLs."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""

    host = parts.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parts.path).rstrip("/").lower()
    if not host or not path:
        return ""

    uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    for ats_host, prefix in (("jobs.ashbyhq.com", "ashby"), ("jobs.lever.co", "lever")):
        if host == ats_host:
            match = re.fullmatch(rf"/([^/]+)/({uuid_pattern})(?:/application|/apply)?", path)
            if match:
                return f"{prefix}:{match.group(1)}:{match.group(2)}"
            return ''  # The company board is not an individual posting.
    if re.fullmatch(r"(?:boards|job-boards)(?:\.[a-z]{2,})?\.greenhouse\.io", host):
        match = re.fullmatch(r"/([^/]+)/jobs/(\d+)(?:/application)?", path)
        if match:
            return f"greenhouse:{match.group(1)}:{match.group(2)}"
        return ''

    if host == "apply.workable.com":
        match = re.fullmatch(r"/(?:[^/]+/)?j/([A-Za-z0-9]{10,})(?:/apply|/application)?", parts.path.rstrip("/"))
        if match:
            return f"workable:{match.group(1)}"
        return ''
    if host.endswith((".jobs.personio.de", ".jobs.personio.com")):
        match = re.fullmatch(r"/job/(\d+)(?:/apply|/application)?", path)
        if match:
            return f"personio:{host}:{match.group(1)}"
        return ''
    if host.endswith(".recruitee.com"):
        match = re.fullmatch(r"/o/([^/]+)(?:/c/new)?", parts.path.rstrip("/"))
        if match:
            return f"recruitee:{host}:{match.group(1)}"
        return ''

    if host == "jobs.smartrecruiters.com":
        path_parts = [part for part in path.split("/") if part]
        if len(path_parts) >= 2:
            match = re.match(r"^(\d+)(?:[-_]|$)", path_parts[1])
            if match:
                return f"smartrecruiters:{path_parts[0]}:{match.group(1)}"

    if host.endswith(".breezy.hr"):
        match = re.search(r"/p/([a-f0-9]{6,})(?:-|/|$)", path)
        if match:
            return f"breezy:{host}:{match.group(1)}"

    if host == "idealist.org":
        match = re.search(r"/(?:nonprofit-job|job)/([a-f0-9]{16,})(?:-|/|$)", path)
        if match:
            return f"idealist:{match.group(1)}"

    # Employer pages and BambooHR copies often differ only by campaign tags.
    # Keep every unknown query parameter: id=123 and id=456 are different jobs.
    if parts.scheme.lower() not in {'http', 'https'} or parts.username or parts.password:
        return ''
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith('utm_')
             and key.lower() not in {'gclid', 'fbclid', 'msclkid', '_ga'}
             and not (host.endswith('.bamboohr.com') and key.lower() == 'source')]
    generic_paths = {'', '/', '/jobs', '/careers', '/vacancies', '/openings', '/positions'}
    functional_id = any(key.lower() in {'id', 'jobid', 'job_id', 'jid', 'postingid', 'ashby_jid', 'gh_jid'} and val
                        for key, val in query)
    if path in generic_paths and not functional_id:
        return ''
    return 'url:' + urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                               parts.path.rstrip('/') or '/', urlencode(sorted(query)), ''))


def _duplicate_url_variants(value: Any) -> list[str]:
    normalized = _normalize_duplicate_url(value)
    if not normalized:
        return []
    return [normalized, f"{normalized}/"]


def _payload_duplicate_key(payload: Dict) -> tuple[str, str, str, str] | None:
    source = _normalize_duplicate_text(payload.get("source"))
    app_url = (
        _canonical_duplicate_url(payload.get("application_url"))
        or _normalize_duplicate_url(payload.get("application_url"))
    )
    title = _normalize_duplicate_title(payload.get("title"))
    organization_name = _normalize_duplicate_text(payload.get("organization_name"))
    if app_url.startswith(UNIQUE_ATS_POSTING_PREFIXES):
        organization_name = "unique-ats-posting"
        title = "unique-ats-posting"
    if not (source and app_url and title and organization_name):
        return None
    return source, app_url, title, organization_name


def _raw_description_signature(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    if len(text) < 80:
        return ""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _payload_raw_description_signature(payload: Dict) -> str:
    raw_data = payload.get("raw_data") or {}
    for key in (
        "description",
        "description_html",
        "description_text",
        "description_plain",
        "description_plaintext",
        "description_plain_text",
    ):
        signature = _raw_description_signature(raw_data.get(key))
        if signature:
            return signature
    return _raw_description_signature(payload.get("description"))


def _job_raw_description_signature(job: Job) -> str:
    raw_data = job.raw_data or {}
    for key in (
        "description",
        "description_html",
        "description_text",
        "description_plain",
        "description_plaintext",
        "description_plain_text",
    ):
        signature = _raw_description_signature(raw_data.get(key))
        if signature:
            return signature
    return _raw_description_signature(job.description)


# Statuses written by check_stale_jobs ("dead") and verify_ats_jobs ("closed", "not_remote").
BLOCKING_VERIFICATION_STATUSES = {"dead", "closed", "not_remote"}


def verification_blocks_listing(raw: Dict) -> bool:
    """A feed sighting must not resurrect a listing the employer's own site says is gone."""
    raw = raw or {}
    return ((raw.get("source_verification") or {}).get("status") in BLOCKING_VERIFICATION_STATUSES
            or (raw.get("source_page_enrichment") or {}).get("status") in {"http_404", "http_410"}
            or (raw.get('location_source_review') or {}).get('needs_review') is True)


def _retired_duplicate_has_available_keeper(job: Job, application_url=None, title=None) -> bool:
    """A fresh feed sighting must not undo a proved canonical retirement.

    A changed posting identity, unrelated editorial redirect, or unavailable
    keeper must still allow the imported record to reopen normally.
    """
    if job.is_active:
        return False
    identity = _canonical_duplicate_url(application_url if application_url is not None else job.application_url)
    if not identity:
        return False
    alias = JobSlugRedirect.objects.filter(old_slug=job.slug).select_related('job__organization').first()
    if not alias or alias.job_id == job.pk or not alias.job.is_available:
        return False
    keeper = alias.job
    return (
        _canonical_duplicate_url(keeper.application_url) == identity
        and (identity.startswith(UNIQUE_ATS_POSTING_PREFIXES) or
             _normalize_duplicate_title(title if title is not None else job.title) == _normalize_duplicate_title(keeper.title))
        and (identity.startswith(UNIQUE_ATS_POSTING_PREFIXES) or keeper.organization_id == job.organization_id)
    )


@transaction.atomic
def _mark_job_seen(source: str, external_id: str, raw_data=None, payload=None) -> bool:
    if source and external_id:
        is_ats = source in {"greenhouse", "lever", "ashby", "workable", "recruitee", "personio", "bamboohr"}
        job = None
        if not is_ats or payload is not None:
            job = Job.objects.select_for_update().filter(source=source, external_id=external_id).only(
                'raw_data', 'is_active', 'slug', 'title', 'application_url', 'organization_id'
            ).first()
        if payload is not None:
            from jobs.services.category_review import category_review_input_hash
            review = ((job.raw_data if job else None) or {}).get('category_source_review')
            if (isinstance(review, dict) and review.get('version') == 1
                    and not review.get('needs_review')
                    and review.get('source_input_hash') != category_review_input_hash(payload)):
                # Let the full upsert invalidate the review and process the changed source.
                return True
            pay_review = ((job.raw_data if job else None) or {}).get('compensation_source_review')
            if (isinstance(pay_review, dict) and pay_review.get('version') == 1
                    and pay_review.get('action') == 'suppress' and not pay_review.get('needs_review')
                    and pay_review.get('source_input_hash') != category_review_input_hash(payload)):
                return True
        updates = {"last_seen_at": timezone.now()}
        # Greenhouse, Lever, and Ashby URLs come from search results. A result
        # may remain indexed after the posting closes, so only the board API is
        # allowed to reactivate these records.
        if not is_ats:
            if (job and source == Job.Source.IDEALIST and isinstance(raw_data, dict)
                    and str(raw_data.get('objectID') or '') == str(external_id)
                    and raw_data.get('orgName') == (job.raw_data or {}).get('orgName')):
                # Incremental imports skip rewriting a job, but source employer
                # classification must still refresh (including removal/changes).
                refreshed = dict(job.raw_data or {})
                for key in ('orgType', 'orgID'):
                    value = raw_data.get(key)
                    if isinstance(value, str) and value:
                        refreshed[key] = value
                    else:
                        refreshed.pop(key, None)
                if refreshed != job.raw_data:
                    updates['raw_data'] = refreshed
            staged_canary = job and ((job.raw_data or {}).get("extraction_canary") or {}).get("phase") == "staged"
            if job and not staged_canary and not verification_blocks_listing(job.raw_data) and not _retired_duplicate_has_available_keeper(job):
                updates["is_active"] = True
        Job.objects.filter(source=source, external_id=external_id).update(**updates)
    return False


async def _async_return(value):
    """Simple async wrapper that returns a value unchanged."""
    return value


def _timestamp_to_datetime(value: Optional[float]) -> datetime:
    if not value:
        return timezone.now()
    # Algolia sometimes returns timestamps in seconds, sometimes milliseconds.
    if value > 10**12:  # milliseconds
        value = value / 1000.0
    return datetime.fromtimestamp(float(value), tz=dt_timezone.utc)


def _get_or_create_category_by_slug(slug: Optional[str]) -> Optional[Category]:
    """Get or create a category from our standard impact areas by slug."""
    if not slug:
        return None

    # Look up in standard impact areas
    area = _IMPACT_AREA_BY_SLUG.get(slug)
    if not area:
        # Fall back to "other" if unknown slug
        area = _IMPACT_AREA_BY_SLUG.get("other")
        if not area:
            return None

    category, created = Category.objects.get_or_create(
        slug=area["slug"],
        defaults={
            "name": area["name"],
            "description": "",
            "icon": area.get("icon", ""),
        },
    )
    if created:
        logger.debug("Created new category %s", category)
    return category


def _get_or_create_category(name: Optional[str]) -> Optional[Category]:
    """
    Get a category by name, matching to standard impact areas.
    Never creates new categories - maps unknown names to 'Other Impact Areas'.
    """
    if not name:
        return None
    
    name_lower = name.lower().strip()
    
    # First, try exact slug match from standard impact areas
    for area in IMPACT_AREAS:
        if area["name"].lower() == name_lower or area["slug"] == name_lower:
            return _get_or_create_category_by_slug(area["slug"])
        
        # Try keyword matching
        if any(kw in name_lower for kw in area.get("keywords", [])):
            return _get_or_create_category_by_slug(area["slug"])
    
    # Try fuzzy matching against existing database categories
    existing_cats = Category.objects.all()
    for cat in existing_cats:
        # Case-insensitive exact match
        if cat.name.lower() == name_lower:
            return cat
        # Partial match (name contains or is contained by)
        if name_lower in cat.name.lower() or cat.name.lower() in name_lower:
            return cat
    
    # No match found - fall back to 'Other Impact Areas'
    logger.info(f"Unknown category '{name}' mapped to 'Other Impact Areas'")
    return _get_or_create_category_by_slug("other")


def _reset_idle_connections():
    """Never close a connection that belongs to an active import transaction."""
    for connection in connections.all(initialized_only=True):
        if not connection.in_atomic_block:
            connection.close_if_unusable_or_obsolete()


def is_duplicate_job(
    application_url: str,
    source: str = None,
    title: str | None = None,
    organization_name: str | None = None,
) -> bool:
    """
    Check if a matching job from another source already exists.

    Matching on title and organization when available avoids treating two
    different roles from the same company board URL as duplicates.
    Returns True if duplicate found (should skip).
    """
    if not application_url:
        return False  # Can't detect duplicates without URL

    # Reset stale connections before database query (important for long-running async imports)
    _reset_idle_connections()
    canonical_url = _canonical_duplicate_url(application_url)
    unique_ats_posting = canonical_url.startswith(UNIQUE_ATS_POSTING_PREFIXES)
    query = Job.objects.filter(
        application_url__in=_duplicate_url_variants(application_url),
        is_active=True,
    )
    if source:
        # Same-source duplicates are handled by _upsert_job so changed upstream
        # IDs can still refresh the existing row instead of being skipped.
        query = query.exclude(source=source)
    if title and not unique_ats_posting:
        query = query.filter(title__iexact=title.strip())
    if organization_name and not unique_ats_posting:
        query = query.filter(organization__name__iexact=organization_name.strip())
    if canonical_url:
        if any(_canonical_duplicate_url(job.application_url) == canonical_url
               for job in query.only('application_url')):
            return True
    elif query.exists():
        return True

    if not canonical_url:
        return False

    canonical_query = Job.objects.filter(is_active=True)
    if source:
        # Same-source duplicates are handled by _upsert_job so changed upstream
        # IDs can still refresh the existing row instead of being skipped.
        canonical_query = canonical_query.exclude(source=source)
    if unique_ats_posting:
        canonical_query = canonical_query.filter(application_url__contains=canonical_url.rsplit(':', 1)[-1])
    elif title:
        canonical_query = canonical_query.filter(title__iexact=title.strip())
    if organization_name and not unique_ats_posting:
        canonical_query = canonical_query.filter(
            organization__name__iexact=organization_name.strip()
        )
    return any(
        _canonical_duplicate_url(job.application_url) == canonical_url
        for job in canonical_query.only("application_url").iterator(chunk_size=500)
    )


def job_exists_in_source(source: str, external_id: str) -> bool:
    """
    Check if a job with this source + external_id already exists.
    Used for incremental imports to skip already-imported jobs.
    """
    if not source or not external_id:
        return False
    # Reset stale connections before database query (important for long-running async imports)
    _reset_idle_connections()
    return Job.objects.filter(source=source, external_id=str(external_id)).exists()


def _sanitize_salary(value) -> float | None:
    """
    Sanitize salary value to prevent overflow in decimal(12,2) field.
    Max valid value: 9,999,999,999.99
    """
    if value is None:
        return None
    try:
        val = float(value)
        # Cap at 10 million - anything higher is likely an error
        if val > 10_000_000:
            logger.warning(f"Salary value {val} too large, setting to None")
            return None
        if val < 0:
            return None
        return val
    except (ValueError, TypeError):
        return None


def _get_or_create_org(
    name: str, website: str = "", description: str = ""
) -> Organization:
    if not name:
        name = "Unknown Organization"
    website = _sanitize_public_url(website)
    org, created = Organization.objects.get_or_create(
        name=name,
        defaults={
            "slug": unique_slug(Organization, name),
            "website": website or "",
            "description": description or "",
        },
    )
    dirty = False
    if website and not org.website:
        org.website = website
        dirty = True
    if description and not org.description:
        org.description = description
        dirty = True
    if created or dirty:
        org.save()
    return org


def _map_job_type(label: Optional[str]) -> str:
    if not label:
        return Job.JOB_TYPE_CHOICES[0][0]
    label_lower = label.lower()
    if "part" in label_lower:
        return "part-time"
    if "contract" in label_lower or "consult" in label_lower:
        return "contract"
    if "freelance" in label_lower:
        return "freelance"
    return "full-time"


def _ensure_job_slug(title: str, organization_name: str) -> str:
    return unique_slug(Job, f"{title}-{organization_name}")


def _find_same_source_duplicate_job(payload: Dict, organization: Organization) -> Optional[Job]:
    app_url = payload.get("application_url", "")
    title = payload.get("title", "")
    url_variants = _duplicate_url_variants(app_url)
    if not (title and payload.get("source")):
        return None

    if url_variants:
        exact_match = (
            Job.objects.filter(
                source=payload["source"],
                organization=organization,
                application_url__in=url_variants,
                title__iexact=title.strip(),
                is_active=True,
            )
            .order_by("-posted_at", "-id")
            .first()
        )
        if exact_match:
            return exact_match

    base_query = Job.objects.filter(
        source=payload["source"],
        organization=organization,
        title__iexact=title.strip(),
        is_active=True,
    )

    canonical_url = _canonical_duplicate_url(app_url)
    if canonical_url:
        canonical_query = base_query
        if canonical_url.startswith(UNIQUE_ATS_POSTING_PREFIXES):
            canonical_query = Job.objects.filter(
                source=payload["source"], is_active=True,
                application_url__contains=canonical_url.rsplit(':', 1)[-1],
            )
        for job in canonical_query.only("id", "application_url", "posted_at").order_by(
            "-posted_at", "-id"
        ):
            if _canonical_duplicate_url(job.application_url) == canonical_url:
                return job

    raw_signature = _payload_raw_description_signature(payload)
    if raw_signature:
        normalized_location = normalize_location(payload.get("location", "Remote"))
        for job in (
            base_query.filter(location=normalized_location)
            .only("id", "raw_data", "description", "posted_at")
            .order_by("-posted_at", "-id")
        ):
            if _job_raw_description_signature(job) == raw_signature:
                return job

    return None


class ExistingJobError(ValueError):
    """A create-only canary must never update an existing listing."""


def _upsert_job(payload: Dict, *, create_only: bool = False) -> Tuple[Job, bool]:
    """
    Persist a job dict returning (job, created?).
    Expected payload keys: see individual importers.

    Note: AI processing should be done via batch_upsert_jobs() before calling this.
    """
    original_payload = dict(payload)
    source_location = payload.get("location")
    source_country = payload.get("country")
    payload = enrich_job_payload(payload)

    organization = _get_or_create_org(
        payload["organization_name"],
        website=payload.get("organization_url", ""),
        description=payload.get("organization_description", ""),
    )

    # Prefer category_slug from AI parsing, fall back to category_name
    category = None
    if payload.get("category_slug"):
        category = _get_or_create_category_by_slug(payload["category_slug"])
    if not category and payload.get("category_name"):
        category = _get_or_create_category(payload["category_name"])

    # Normalize location to standard country/region value
    raw_location = payload.get("location", "Remote")
    normalized_location = normalize_location(raw_location)

    defaults = {
        "title": payload["title"],
        "description": payload.get("description", ""),
        "requirements": payload.get("requirements", "")
        or payload.get("description", ""),
        "location": normalized_location,
        "job_type": payload.get("job_type", "full-time"),
        "application_url": payload.get("application_url", ""),
        "application_email": payload.get("application_email", ""),
        "salary_min": _sanitize_salary(payload.get("salary_min")),
        "salary_max": _sanitize_salary(payload.get("salary_max")),
        "salary_currency": payload.get("salary_currency") or ("USD" if (_sanitize_salary(payload.get("salary_min")) or _sanitize_salary(payload.get("salary_max"))) else ""),
        "impact": payload.get("impact", ""),
        "benefits": payload.get("benefits", ""),
        "company_description": payload.get("company_description", ""),
        "how_to_apply_text": payload.get("how_to_apply_text", ""),
        "skills": payload.get("skills", []),  # AI-extracted skills for matching
        "experience_level": payload.get("experience_level"),
        "education_level": payload.get("education_level"),
        "country": payload.get("country"),
        "is_active": payload.get("is_active", True),
        "is_featured": payload.get("is_featured", False),
        "posted_at": payload.get("posted_at", timezone.now()),
        "expires_at": payload.get("expires_at"),
        "raw_data": payload.get("raw_data", {}),
        "last_seen_at": timezone.now(),
    }
    # Reviews are local editorial evidence, never instructions from a feed.
    defaults['raw_data'] = dict(defaults['raw_data'] or {})
    defaults['raw_data'].pop('location_source_review', None)
    defaults['raw_data'].pop('category_source_review', None)
    defaults['raw_data'].pop('compensation_source_review', None)
    drop_implausible_salary(defaults)

    job = Job.objects.filter(
        source=payload["source"],
        external_id=payload["external_id"],
    ).first()

    if not job:
        job = _find_same_source_duplicate_job(payload, organization)

    if job:
        if create_only:
            raise ExistingJobError("Existing job skipped by create-only import")
        if not (original_payload.get("raw_data") or {}).get("job_facts"):
            from jobs.services.extraction_refresh import retained_facts
            retained = retained_facts(job, original_payload)
            if retained:
                from jobs.services.job_facts import legacy_fields
                from jobs.services.raw_data_extractor import RawDataExtractor
                trusted = RawDataExtractor.extract(original_payload.get("source", ""), original_payload.get("raw_data") or {})
                values = legacy_fields(retained)
                adopted = dict(original_payload)
                raw = dict(adopted.get("raw_data") or {})
                raw["job_facts"] = retained
                canary = (job.raw_data or {}).get("extraction_canary")
                if canary:
                    raw["extraction_canary"] = dict(canary)
                    if canary.get("phase") == "staged":
                        adopted["is_active"] = False
                adopted["raw_data"] = raw
                adopted["skills"] = values["skills"]
                for field in ("experience_level", "education_level", "job_type", "salary_min", "salary_max", "salary_currency"):
                    adopted[field] = trusted.get(field) if trusted.get(field) is not None else values.get(field)
                if not adopted.get("job_type"):
                    adopted["job_type"] = original_payload.get("job_type") or job.job_type
                if values.get("impact_area"):
                    adopted["category_slug"] = values["impact_area"]
                else:
                    adopted.pop("category_slug", None)
                result = _upsert_job(adopted, create_only=create_only)
                from django.core.cache import caches
                from jobs.services.extraction_cache import TTL
                def refresh_cache():
                    try:
                        caches['job_extraction'].set(retained['cache_key'], retained, timeout=TTL)
                    except Exception:
                        logger.warning('Extraction cache refresh unavailable')
                transaction.on_commit(refresh_cache)
                return result
        old_title = job.title
        old_raw = job.raw_data or {}
        prior_canary = old_raw.get("extraction_canary") or {}
        if prior_canary.get("phase") == "staged":
            defaults["is_active"] = False
        if (payload['source'] == Job.Source.BAMBOOHR and ':' in str(payload['external_id'])
                and _canonical_duplicate_url(job.application_url)
                == _canonical_duplicate_url(defaults['application_url'])):
            # Adopt the board-scoped identity when refreshing an exact legacy
            # destination; titles/companies never authorize a cross-board move.
            job.external_id = payload['external_id']
        same_destination = job.application_url == defaults["application_url"]
        confirmed_dead = same_destination and verification_blocks_listing(old_raw)
        retired_duplicate = _retired_duplicate_has_available_keeper(job, defaults['application_url'], defaults['title'])
        if confirmed_dead or retired_duplicate:
            defaults["is_active"] = False
        if (defaults['raw_data'] or {}).get('posted_at_basis') == 'first_seen':
            # Missing employer dates do not make a previously seen role new.
            defaults['posted_at'] = job.posted_at
        if not source_location and job.location:
            defaults["location"] = job.location
        if not source_country and defaults["country"] == "Worldwide" and source_location in (None, "", "Remote") and job.country:
            defaults["country"] = job.country
        for field in ("description", "requirements", "country", "skills", "experience_level", "education_level", "benefits"):
            if defaults[field] in (None, "", []) and getattr(job, field):
                defaults[field] = getattr(job, field)
        if len(defaults["description"].strip()) < PUBLIC_DESCRIPTION_MIN_LENGTH and len((job.description or "").strip()) >= PUBLIC_DESCRIPTION_MIN_LENGTH:
            defaults["description"] = job.description
            defaults["requirements"] = job.requirements
            defaults["skills"] = sorted(set(job.skills or []) | set(defaults["skills"] or []))
        if defaults["salary_min"] is None and defaults["salary_max"] is None:
            for field in ("salary_min", "salary_max", "salary_currency"):
                defaults[field] = getattr(job, field)
        drop_implausible_salary(defaults)
        incoming_raw = dict(defaults["raw_data"] or {})
        # A locally reviewed pay suppression survives feed refreshes. Preserve
        # original feed fields, but don't advertise unverified numeric pay.
        pay_review = old_raw.get('compensation_source_review')
        if (isinstance(pay_review, dict) and pay_review.get('version') == 1
                and pay_review.get('action') == 'suppress'):
            from jobs.services.category_review import category_review_input_hash
            incoming_raw['compensation_source_review'] = {
                **pay_review,
                'needs_review': (pay_review.get('needs_review', False)
                    or pay_review.get('source_input_hash') != category_review_input_hash(original_payload)),
            }
            defaults['salary_min'] = defaults['salary_max'] = None
            defaults['salary_currency'] = ''
        # Only a locally recorded review of this exact source input may retain
        # an editorial correction. A changed posting requires fresh review.
        from jobs.services.category_review import retained_category_review
        category_review = retained_category_review(job, original_payload)
        if category_review:
            category = job.category
            incoming_raw['category_source_review'] = category_review
        elif isinstance(old_raw.get('category_source_review'), dict):
            incoming_raw['category_source_review'] = {
                **old_raw['category_source_review'], 'needs_review': True,
            }
        # An unchanged aggregator label must not immediately undo a recent
        # employer-source correction. Changed source inputs require a new review.
        from jobs.services.location_review import retained_location_review
        location_review = retained_location_review(job, original_payload)
        incoming_raw.pop('location_source_review', None)
        if location_review:
            correction = location_review['correction']
            defaults['location'] = correction['location']
            defaults['country'] = correction['country']
            incoming_raw['location_source_review'] = location_review
            if correction.get('jobLocation'):
                incoming_raw['jobLocation'] = correction['jobLocation']
        elif (isinstance(old_raw.get('location_source_review'), dict)
              and old_raw['location_source_review'].get('version') == 1):
            incoming_raw['location_source_review'] = {
                **old_raw['location_source_review'], 'needs_review': True,
            }
            defaults['is_active'] = False
        elif isinstance(old_raw.get('location_source_review'), dict):
            incoming_raw['location_source_review'] = old_raw['location_source_review']
        for key in ("quality_enrichment", "source_page_enrichment", "source_verification", "last_stale_check", "crawl_error", "ats", "ats_check"):
            if key in old_raw and key not in incoming_raw and (same_destination or key == "quality_enrichment"):
                incoming_raw[key] = old_raw[key]
        if "job_facts" not in incoming_raw and isinstance(old_raw.get("job_facts"), dict):
            from jobs.services.job_facts import source_description, source_hash
            old_facts = old_raw["job_facts"]
            if old_facts.get("source_hash") == source_hash(
                payload.get("title", ""), payload.get("organization_name", ""), source_description(payload)
            ):
                incoming_raw["job_facts"] = old_facts
        if prior_canary and "extraction_canary" not in incoming_raw:
            incoming_raw["extraction_canary"] = dict(prior_canary)
            if "job_facts" not in incoming_raw:
                incoming_raw["extraction_canary"].update(
                    needs_review=True,
                    phase="staged" if prior_canary.get("phase") == "staged" else "source_changed",
                )
        defaults["raw_data"] = incoming_raw
        if (job.category_id and (category is None or category.slug == "other")
                and not (old_raw.get('category_source_review') and not category_review)):
            category = job.category
        for field, value in defaults.items():
            setattr(job, field, value)
        # Do not let a transiently incomplete refresh hide a previously valid
        # active role. New records are gated below; crawler updates explicitly
        # reactivate a record once its detail page is complete.
        if job.is_active and not _public_listing_quality_ready(payload):
            job.is_active = True
        job.organization = organization
        job.category = category
        # Update slug if title changed from placeholder
        new_title = defaults.get("title", old_title)
        if old_title != new_title and job.slug.startswith("job-at-"):
            job.slug = _ensure_job_slug(new_title, organization.name)
        refresh_job_quality(job)
        job.save()
        return job, False

    slug = _ensure_job_slug(payload["title"], organization.name)
    if not _public_listing_quality_ready(payload):
        defaults["is_active"] = False
        raw_data = dict(defaults.get("raw_data") or {})
        quality = dict(raw_data.get("quality_enrichment") or {})
        quality["publication_gate"] = {
            "method": "minimum_listing_quality_v1",
            "description_min_length": PUBLIC_DESCRIPTION_MIN_LENGTH,
            "has_application_destination": bool(
                defaults.get("application_url") or defaults.get("application_email")
            ),
            "needs_crawling": bool(raw_data.get("needs_crawling")),
        }
        raw_data["quality_enrichment"] = quality
        defaults["raw_data"] = raw_data
    job = Job(
        slug=slug,
        organization=organization,
        category=category,
        source=payload["source"],
        external_id=payload["external_id"],
        **defaults,
    )
    refresh_job_quality(job)
    job.save()
    return job, True


def _upsert_job_sync(payload: Dict) -> Tuple[Job, bool]:
    """Synchronous wrapper for _upsert_job with transaction."""
    # Reset stale connections before database write (important for long-running async imports)
    _reset_idle_connections()
    with transaction.atomic():
        return _upsert_job(payload)


async def batch_upsert_jobs(
    payloads: List[Dict],
    use_ai: bool = False,
    batch_size: int = 50,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    skip_duplicates: bool = True,
    skip_existing: bool = False,
    provider: Optional[str] = None,
) -> Dict[str, int]:
    """
    Upsert multiple jobs with optional batch AI processing.

    Processes and saves jobs in batches for better performance and incremental saving.

    Args:
        payloads: List of job payload dicts from importers
        use_ai: Whether to use AI to enrich job descriptions
        batch_size: Number of jobs per batch (default: 50)
        progress_callback: Optional callback(completed, total) for progress updates
        skip_duplicates: Skip jobs that already exist with same URL from different source
        skip_existing: Skip jobs that already exist in the same source (for incremental imports)
        provider: DeepSeek, or None for the production default

    Returns:
        Dict with keys: created, updated, fetched, skipped
    """
    if not payloads:
        return {"fetched": 0, "created": 0, "updated": 0, "skipped": 0}

    stats = {"fetched": len(payloads), "created": 0, "updated": 0, "skipped": 0, "failed": 0}

    from jobs.services.scout_verification import non_opening
    publishable_payloads = [
        payload for payload in payloads if not _is_generic_listing_title(payload.get("title"))
        and not non_opening(payload.get('title'), payload.get('application_url'))
    ]
    stats["skipped"] += len(payloads) - len(publishable_payloads)
    payloads = publishable_payloads

    if not payloads:
        return stats

    # Skip jobs that already exist in the same source (for incremental imports)
    if skip_existing:
        check_exists = sync_to_async(job_exists_in_source, thread_sensitive=True)
        filtered_payloads = []
        for payload in payloads:
            source = payload.get("source", "")
            external_id = payload.get("external_id", "")
            if source and external_id and await check_exists(source, external_id):
                needs_refresh = await sync_to_async(_mark_job_seen, thread_sensitive=True)(
                    source, external_id, payload.get("raw_data"), payload)
                if needs_refresh:
                    filtered_payloads.append(payload)
                else:
                    stats["skipped"] += 1
            else:
                filtered_payloads.append(payload)
        payloads = filtered_payloads
        if stats["skipped"] > 0:
            logger.info(f"Skipped {stats['skipped']} existing jobs (already imported)")

    # Filter out duplicates from other sources before AI processing (saves API costs)
    if skip_duplicates:
        check_duplicate = sync_to_async(is_duplicate_job, thread_sensitive=True)
        filtered_payloads = []
        seen_duplicate_keys = set()
        for payload in payloads:
            duplicate_key = _payload_duplicate_key(payload)
            if duplicate_key and duplicate_key in seen_duplicate_keys:
                stats["skipped"] += 1
                logger.debug("Skipping repeated payload for %s", payload.get("title", "Untitled"))
                continue
            if duplicate_key:
                seen_duplicate_keys.add(duplicate_key)

            app_url = payload.get("application_url", "")
            source = payload.get("source", "")
            if app_url and await check_duplicate(
                app_url,
                source,
                payload.get("title"),
                payload.get("organization_name"),
            ):
                stats["skipped"] += 1
                logger.debug(f"Skipping duplicate URL: {app_url[:50]}...")
            else:
                filtered_payloads.append(payload)
        payloads = filtered_payloads
        if stats["skipped"] > 0:
            logger.info(f"Skipped {stats['skipped']} duplicate jobs (same application URL)")

    if not payloads:
        return stats

    total = len(payloads)
    completed = 0
    upsert_async = sync_to_async(_upsert_job_sync, thread_sensitive=True)

    # Initialize parser once if using AI
    parser = None
    if use_ai:
        try:
            from jobs.services.llm_parser import JobParser
            parser = JobParser(provider=provider)
            provider_info = f" with {provider}" if provider else f" with {parser.provider_name}"
            logger.info(f"Processing {total} jobs with AI{provider_info} (batch_size={batch_size})...")
        except Exception as e:
            logger.error(f"Failed to initialize AI parser: {e}")
            use_ai = False

    # Process in batches - AI parse and save each batch immediately
    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = payloads[batch_start:batch_end]

        # AI process this batch if enabled
        if use_ai and parser:
            try:
                # Process batch with high concurrency
                tasks = []
                for payload in batch:
                    title = payload.get("title", "Untitled")
                    org = payload.get("organization_name", "Unknown")
                    from jobs.services.job_facts import source_description
                    desc = source_description(payload)
                    if len(desc) >= 50:
                        tasks.append(parser._parse_and_enrich(payload, title, org, desc))
                    else:
                        tasks.append(_async_return(payload))

                # Run all AI calls in parallel
                batch = await asyncio.gather(*tasks, return_exceptions=True)
                batch = [b if not isinstance(b, Exception) else payloads[batch_start + i]
                        for i, b in enumerate(batch)]
            except Exception as e:
                logger.error(f"AI batch processing failed: {e}")
                # Continue with original batch

        # Save this batch to database immediately
        save_tasks = [upsert_async(payload) for payload in batch]
        results = await asyncio.gather(*save_tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Failed to save job: {result}")
                stats["failed"] += 1
            else:
                job, created = result
                if created:
                    stats["created"] += 1
                else:
                    stats["updated"] += 1

            completed += 1
            if progress_callback:
                progress_callback(completed, total)

        # Brief delay between batches to avoid overwhelming the system
        if batch_end < total:
            await asyncio.sleep(0.1)

    logger.info(
        "Import complete: %s created, %s updated, %s failed",
        stats["created"], stats["updated"], stats["failed"],
    )
    return stats


def _get_ai_parser(provider: Optional[str] = None):
    """Get an AI parser instance, or None if unavailable."""
    try:
        from jobs.services.llm_parser import JobParser
        return JobParser(provider=provider)
    except Exception as e:
        logger.error(f"Failed to initialize AI parser: {e}")
        return None


async def batch_process_with_ai(
    payloads: List[Dict],
    batch_size: int = 20,
    provider: Optional[str] = None,
) -> List[Dict]:
    """
    Process job payloads with AI enrichment.

    Args:
        payloads: List of dicts with job_id, title, description, etc.
        batch_size: Number of concurrent AI requests
        provider: LLM provider to use

    Returns:
        List of enriched payloads with job_id preserved
    """
    parser = _get_ai_parser(provider)
    if not parser:
        logger.warning("No AI parser available, returning original payloads")
        return payloads

    enriched = []
    total = len(payloads)

    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = payloads[batch_start:batch_end]

        tasks = []
        for payload in batch:
            title = payload.get("title", "Untitled")
            org = payload.get("organization_name", "Unknown")
            desc = payload.get("description", "")

            if len(desc) >= 50:
                # Create a modified payload for AI processing
                ai_payload = {**payload}
                tasks.append(_enrich_single_job(parser, ai_payload, title, org, desc))
            else:
                tasks.append(_async_return(payload))

        # Run all AI calls in parallel
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"AI enrichment failed: {result}")
                    enriched.append(batch[i])
                else:
                    enriched.append(result)
        except Exception as e:
            logger.error(f"AI batch processing failed: {e}")
            enriched.extend(batch)

        logger.info(f"AI enriched batch: {batch_end}/{total} jobs")

    return enriched


async def _enrich_single_job(
    parser, payload: Dict, title: str, org: str, desc: str
) -> Dict:
    """Enrich a single job with AI."""
    try:
        enriched = await parser._parse_and_enrich(payload, title, org, desc)
        # Preserve the job_id
        if "job_id" in payload:
            enriched["job_id"] = payload["job_id"]
        return enriched
    except Exception as e:
        logger.error(f"Error enriching job {title}: {e}")
        return payload


def _algolia_headers(app_id: str, api_key: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Algolia-Application-Id": app_id,
        "X-Algolia-API-Key": api_key,
    }


def _algolia_url(app_id: str) -> str:
    return f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/*/queries"


def _paginate_algolia(
    url: str,
    headers: Dict[str, str],
    base_request: Dict,
    result_index: int = 0,
) -> Iterable[List[Dict]]:
    page = 0
    while True:
        payload = deepcopy(base_request)
        payload["requests"][0]["page"] = page
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        result = data["results"][result_index]
        yield result.get("hits", [])
        page += 1
        if page >= result.get("nbPages", 0):
            break


FEED_SOURCES = {"80000hours", "idealist", "reliefweb", "climatebase", "probablygood"}


def retire_unseen_listings(source: str, run_started: datetime, fetched: int, grace_days: int = 2) -> dict:
    """Take down listings a full feed fetch no longer contains.

    Aggregators drop a listing when the employer closes it. Every successful fetch
    marks the listings it contains as seen, so anything unseen for `grace_days`
    has been removed upstream. A sighting later reactivates it (see _mark_job_seen).
    Listings the employer's own ATS confirmed open recently are kept.
    """
    from datetime import timedelta

    if source not in FEED_SOURCES:
        return {"retired": 0, "skipped": "not a feed source"}
    if fetched < 20:
        return {"retired": 0, "skipped": f"only {fetched} fetched"}
    recent = Job.objects.filter(
        source=source, is_active=True, last_seen_at__gte=run_started - timedelta(days=grace_days)
    ).count()
    # Guard against partial or broken fetches: a feed that suddenly returns far fewer
    # listings than it showed in the last couple of days is not trusted to retire anything.
    if fetched < 0.5 * recent:
        return {"retired": 0, "skipped": f"fetched {fetched} vs {recent} recently seen"}
    cutoff = run_started - timedelta(days=grace_days)
    ats_fresh = (run_started - timedelta(days=3)).isoformat()
    stale = Job.objects.filter(source=source, is_active=True).filter(
        Q(last_seen_at__lt=cutoff) | Q(last_seen_at__isnull=True, posted_at__lt=cutoff)
    )
    retired = 0
    now = timezone.now()
    for job in stale.only("id", "raw_data").iterator(chunk_size=500):
        raw = dict(job.raw_data or {})
        check = raw.get("source_verification") or {}
        if check.get("method") == "ats_api" and check.get("status") == "open" and str(check.get("checked_at", "")) >= ats_fresh:
            continue
        raw["removed_from_source_at"] = now.isoformat()
        Job.objects.filter(pk=job.pk).update(is_active=False, raw_data=raw)
        retired += 1
    return {"retired": retired, "skipped": ""}
