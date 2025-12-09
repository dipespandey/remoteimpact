from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

from django.conf import settings

from jobs.models import Job
from .common import (
    _algolia_headers,
    _algolia_url,
    _map_job_type,
    _paginate_algolia,
    _timestamp_to_datetime,
    batch_upsert_jobs,
)

logger = logging.getLogger(__name__)


def _build_idealist_payload() -> Dict:
    return {
        "requests": [
            {
                "indexName": "idealist7-production",
                "hitsPerPage": 100,
                "page": 0,
                "filters": "type:JOB AND locationType:REMOTE",
                "attributesToRetrieve": [
                    "name",
                    "description",
                    "url",
                    "orgName",
                    "orgUrl",
                    "orgDescription",
                    "jobType",
                    "areasOfFocus",
                    "remoteCountry",
                    "remoteState",
                    "remoteZone",
                    "salaryMinimum",
                    "salaryMaximum",
                    "salaryCurrency",
                    "salaryPeriod",
                    "hasSalary",
                    "published",
                    "objectID",
                ],
            }
        ]
    }


def _transform_idealist_hit(hit: Dict) -> Dict:
    location_bits = ["Remote"]
    if hit.get("remoteCountry"):
        location_bits.append(hit["remoteCountry"])
    elif hit.get("remoteZone"):
        location_bits.append(hit["remoteZone"])
    location = " · ".join(location_bits)

    if isinstance(hit.get("url"), dict):
        primary_url = (
            hit["url"].get("en") or hit["url"].get("es") or hit["url"].get("pt")
        )
    else:
        primary_url = hit.get("url")
    application_url = primary_url
    if application_url and application_url.startswith("/"):
        application_url = f"https://www.idealist.org{application_url}"

    areas = hit.get("areasOfFocus") or ["Impact Careers"]
    if isinstance(areas, str):
        areas = [areas]
    category_name = areas[0].replace("_", " ").title()

    job_type_value = hit.get("jobType") or ""
    if isinstance(job_type_value, list):
        job_type_value = job_type_value[0] if job_type_value else ""
    job_type_label = str(job_type_value).replace("_", " ").lower()

    salary_min = hit.get("salaryMinimum")
    salary_max = hit.get("salaryMaximum")

    return {
        "source": Job.Source.IDEALIST,
        "external_id": hit.get("objectID"),
        "title": hit.get("name", "Untitled role"),
        "description": hit.get("description", ""),
        "requirements": hit.get("description", ""),
        "location": location,
        "job_type": _map_job_type(job_type_label),
        "application_url": application_url or "",
        "application_email": "",
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": (hit.get("salaryCurrency") or "USD")[:3],
        "posted_at": _timestamp_to_datetime(hit.get("published")),
        "expires_at": None,
        "category_name": category_name,
        "organization_name": hit.get("orgName") or "Unknown Organization",
        "organization_description": hit.get("orgDescription", ""),
        "organization_url": hit.get("orgUrl", ""),
        "is_featured": False,
        "raw_data": hit,
    }


async def import_idealist(
    limit: Optional[int] = None,
    dry_run: bool = False,
    use_ai: bool = False,
    batch_size: int = 20,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, int]:
    """
    Import remote jobs from Idealist.

    Args:
        limit: Maximum number of jobs to import
        dry_run: If True, fetch but don't save to database
        use_ai: If True, use AI to enrich job descriptions
        batch_size: Number of concurrent AI requests (default: 20)
        progress_callback: Optional callback(completed, total) for progress updates

    Returns:
        Dict with keys: fetched, created, updated
    """
    app_id = settings.IDEALIST_ALGOLIA_APP_ID
    api_key = settings.IDEALIST_ALGOLIA_API_KEY
    headers = _algolia_headers(app_id, api_key)
    url = _algolia_url(app_id)
    payload = _build_idealist_payload()

    # Collect all job payloads first
    all_payloads = []
    for hits in _paginate_algolia(url, headers, payload):
        for hit in hits:
            job_payload = _transform_idealist_hit(hit)
            all_payloads.append(job_payload)
            if limit and len(all_payloads) >= limit:
                break
        if limit and len(all_payloads) >= limit:
            break

    if dry_run:
        return {"fetched": len(all_payloads), "created": 0, "updated": 0}

    # Batch upsert with optional AI processing
    stats = await batch_upsert_jobs(
        all_payloads,
        use_ai=use_ai,
        batch_size=batch_size,
        progress_callback=progress_callback,
    )
    return stats
