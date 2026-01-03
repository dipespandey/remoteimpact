from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

import requests
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


def _remote_80k_facets(url: str, headers: Dict[str, str]) -> List[str]:
    payload = {
        "requests": [
            {
                "indexName": "jobs_prod",
                "facets": ["tags_location_80k"],
                "hitsPerPage": 0,
                "page": 0,
            }
        ]
    }
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    result = response.json()["results"][0]
    facets = result.get("facets", {}).get("tags_location_80k", {})
    remote_tags = [name for name in facets.keys() if name.lower().startswith("remote")]
    if not remote_tags:
        remote_tags = ["Remote, Global"]
    return remote_tags


def _build_80k_payload(remote_tags: List[str]) -> Dict:
    filters = " OR ".join([f'tags_location_80k:"{tag}"' for tag in remote_tags])
    return {
        "requests": [
            {
                "indexName": "jobs_prod",
                "hitsPerPage": 100,
                "page": 0,
                "filters": filters,
                "attributesToRetrieve": [
                    "title",
                    "description",
                    "description_short",
                    "url_external",
                    "tags_role_type",
                    "tags_area",
                    "tags_location_80k",
                    "company_name",
                    "company_description",
                    "company_url",
                    "salary_limit",
                    "salary_currency",
                    "salary",
                    "posted_at",
                    "closes_at",
                    "id_external_80_000_hours",
                    "objectID",
                ],
            }
        ]
    }


def _transform_80k_hit(hit: Dict) -> Optional[Dict]:
    remote_labels = hit.get("tags_location_80k", [])
    if not any("remote" in label.lower() for label in remote_labels):
        return None
    location = next(
        (label for label in remote_labels if "remote" in label.lower()),
        remote_labels[0] if remote_labels else "Remote",
    )
    category_name = (hit.get("tags_area") or ["Impact Careers"])[0]
    job_type_label = (hit.get("tags_role_type") or ["Full-time"])[0]
    description = hit.get("description") or hit.get("description_short") or ""
    return {
        "source": Job.Source.EIGHTY_THOUSAND,
        "external_id": hit.get("id_external_80_000_hours") or hit.get("objectID"),
        "title": hit.get("title", "Untitled role"),
        "description": description,
        "requirements": hit.get("description_short") or description,
        "location": location or "Remote",
        "job_type": _map_job_type(job_type_label),
        "application_url": hit.get("url_external") or hit.get("company_url") or "",
        "application_email": "",
        "salary_min": None,
        "salary_max": hit.get("salary_limit"),
        "salary_currency": (hit.get("salary_currency") or "USD")[:3],
        "posted_at": _timestamp_to_datetime(hit.get("posted_at")),
        "expires_at": _timestamp_to_datetime(hit.get("closes_at"))
        if hit.get("closes_at")
        else None,
        "category_name": category_name,
        "organization_name": hit.get("company_name") or "Unknown Organization",
        "organization_description": hit.get("company_description", ""),
        "organization_url": hit.get("company_url", ""),
        "is_featured": bool(hit.get("highlighted")),
        "raw_data": hit,
    }


async def import_80000_hours(
    limit: Optional[int] = None,
    dry_run: bool = False,
    use_ai: bool = False,
    batch_size: int = 20,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    provider: Optional[str] = None,
    skip_existing: bool = False,
) -> Dict[str, int]:
    """
    Import remote jobs from 80,000 Hours.

    Args:
        limit: Maximum number of jobs to import
        dry_run: If True, fetch but don't save to database
        use_ai: If True, use AI to enrich job descriptions
        batch_size: Number of concurrent AI requests (default: 20)
        progress_callback: Optional callback(completed, total) for progress updates
        provider: LLM provider ('deepseek', 'groq', 'mistral', or None for auto)
        skip_existing: If True, skip jobs already imported (for incremental imports)

    Returns:
        Dict with keys: fetched, created, updated
    """
    app_id = settings.EIGHTYK_ALGOLIA_APP_ID
    api_key = settings.EIGHTYK_ALGOLIA_API_KEY
    headers = _algolia_headers(app_id, api_key)
    url = _algolia_url(app_id)

    remote_tags = _remote_80k_facets(url, headers)
    payload = _build_80k_payload(remote_tags)

    # Collect all job payloads first
    all_payloads = []
    for hits in _paginate_algolia(url, headers, payload):
        for hit in hits:
            job_payload = _transform_80k_hit(hit)
            if not job_payload:
                continue
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
        provider=provider,
        skip_existing=skip_existing,
    )
    return stats
