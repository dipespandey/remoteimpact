from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timezone as dt_timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import requests
from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from jobs.models import Category, Job, Organization
from jobs.utils import unique_slug
from jobs.constants import IMPACT_AREAS

logger = logging.getLogger(__name__)

# Build lookup for standard impact areas
_IMPACT_AREA_BY_SLUG = {area["slug"]: area for area in IMPACT_AREAS}


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
    """Legacy: Get or create a category by name."""
    if not name:
        return None
    category, created = Category.objects.get_or_create(
        name=name,
        defaults={
            "slug": unique_slug(Category, name),
            "description": "",
        },
    )
    if created:
        logger.debug("Created new category %s", category)
    return category


def is_duplicate_job(application_url: str, source: str = None) -> bool:
    """
    Check if a job with the same application URL already exists.
    Returns True if duplicate found (should skip).
    """
    if not application_url:
        return False  # Can't detect duplicates without URL

    query = Job.objects.filter(
        application_url=application_url.strip(),
        is_active=True,
    )
    if source:
        # Exclude jobs from the same source (they'll be updated, not duplicated)
        query = query.exclude(source=source)
    return query.exists()


def job_exists_in_source(source: str, external_id: str) -> bool:
    """
    Check if a job with this source + external_id already exists.
    Used for incremental imports to skip already-imported jobs.
    """
    if not source or not external_id:
        return False
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


def _upsert_job(payload: Dict) -> Tuple[Job, bool]:
    """
    Persist a job dict returning (job, created?).
    Expected payload keys: see individual importers.

    Note: AI processing should be done via batch_upsert_jobs() before calling this.
    """
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

    defaults = {
        "title": payload["title"],
        "description": payload.get("description", ""),
        "requirements": payload.get("requirements", "")
        or payload.get("description", ""),
        "location": payload.get("location", "Remote"),
        "job_type": payload.get("job_type", "full-time"),
        "application_url": payload.get("application_url", ""),
        "application_email": payload.get("application_email", ""),
        "salary_min": _sanitize_salary(payload.get("salary_min")),
        "salary_max": _sanitize_salary(payload.get("salary_max")),
        "salary_currency": payload.get("salary_currency", "USD"),
        "impact": payload.get("impact", ""),
        "benefits": payload.get("benefits", ""),
        "company_description": payload.get("company_description", ""),
        "how_to_apply_text": payload.get("how_to_apply_text", ""),
        "skills": payload.get("skills", []),  # AI-extracted skills for matching
        "is_active": True,
        "is_featured": payload.get("is_featured", False),
        "posted_at": payload.get("posted_at", timezone.now()),
        "expires_at": payload.get("expires_at"),
        "raw_data": payload.get("raw_data", {}),
    }

    job = Job.objects.filter(
        source=payload["source"],
        external_id=payload["external_id"],
    ).first()

    if job:
        for field, value in defaults.items():
            setattr(job, field, value)
        job.organization = organization
        job.category = category
        job.save()
        return job, False

    slug = _ensure_job_slug(payload["title"], organization.name)
    job = Job(
        slug=slug,
        organization=organization,
        category=category,
        source=payload["source"],
        external_id=payload["external_id"],
        **defaults,
    )
    job.save()
    return job, True


def _upsert_job_sync(payload: Dict) -> Tuple[Job, bool]:
    """Synchronous wrapper for _upsert_job with transaction."""
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
        provider: LLM provider to use ('deepseek', 'groq', 'mistral', or None for auto)

    Returns:
        Dict with keys: created, updated, fetched, skipped
    """
    if not payloads:
        return {"fetched": 0, "created": 0, "updated": 0, "skipped": 0}

    stats = {"fetched": len(payloads), "created": 0, "updated": 0, "skipped": 0}

    # Skip jobs that already exist in the same source (for incremental imports)
    if skip_existing:
        check_exists = sync_to_async(job_exists_in_source, thread_sensitive=True)
        filtered_payloads = []
        for payload in payloads:
            source = payload.get("source", "")
            external_id = payload.get("external_id", "")
            if source and external_id and await check_exists(source, external_id):
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
        for payload in payloads:
            app_url = payload.get("application_url", "")
            source = payload.get("source", "")
            if app_url and await check_duplicate(app_url, source):
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
                    desc = payload.get("description", "")
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

    logger.info(f"Import complete: {stats['created']} created, {stats['updated']} updated")
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
