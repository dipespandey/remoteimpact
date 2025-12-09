from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone
from typing import Callable, Dict, Optional, Tuple

import requests
from django.conf import settings
from django.utils import timezone

from jobs.models import Job
from .common import _map_job_type, batch_upsert_jobs

logger = logging.getLogger(__name__)

API_URL = "https://api.reliefweb.int/v2/jobs"
PAGE_SIZE = 100
REMOTE_QUERY = "(remote) AND NOT _exists_:country"


def _reliefweb_headers(app_name: str) -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": app_name,
    }


def _parse_iso_date(value: Optional[str]) -> datetime:
    if not value:
        return timezone.now()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_timezone.utc)
        return parsed
    except Exception:  # pragma: no cover - defensive fallback
        logger.warning("Could not parse ReliefWeb date %s", value)
        return timezone.now()


def _fetch_reliefweb_list(
    app_name: str, offset: int, headers: Dict[str, str]
) -> Tuple[list, Optional[int]]:
    params = {
        "appname": app_name,
        "profile": "list",
        "preset": "latest",
        "slim": 1,
        "query[value]": REMOTE_QUERY,
        "query[operator]": "AND",
        "limit": PAGE_SIZE,
        "offset": offset,
    }
    response = requests.get(API_URL, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()
    items = data.get("data", [])
    total_count = data.get("totalCount")
    return items, total_count


def _fetch_reliefweb_detail(
    job_id: str, app_name: str, headers: Dict[str, str]
) -> Optional[Dict]:
    params = {"appname": app_name}
    response = requests.get(
        f"{API_URL}/{job_id}", params=params, headers=headers, timeout=30
    )
    response.raise_for_status()
    data = response.json()
    items = data.get("data") or []
    return items[0] if items else None


def _transform_reliefweb_item(item: Dict) -> Dict:
    fields = item.get("fields", {})
    title = fields.get("title") or "Untitled role"
    description = fields.get("body") or ""

    categories = fields.get("career_categories") or fields.get("theme") or []
    if isinstance(categories, dict):
        categories = [categories]
    category_name = "Impact Careers"
    if categories:
        first = categories[0]
        category_name = first.get("name") or category_name

    organization_name = "Unknown Organization"
    organization_url = ""
    sources = fields.get("source") or []
    if isinstance(sources, dict):
        sources = [sources]
    if sources:
        org = sources[0]
        organization_name = org.get("name") or org.get("shortname") or organization_name
        organization_url = org.get("homepage") or ""

    date_info = fields.get("date") or {}
    created_at = date_info.get("created")
    closing_at = date_info.get("closing")

    application_url = fields.get("url") or fields.get("url_alias") or ""
    if not application_url:
        redirects = fields.get("redirects") or []
        if redirects:
            application_url = redirects[0]

    return {
        "source": Job.Source.RELIEFWEB,
        "external_id": str(item.get("id")),
        "title": title,
        "description": description,
        "requirements": description,
        "location": "Remote",
        "job_type": _map_job_type(""),
        "application_url": application_url,
        "application_email": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "USD",
        "posted_at": _parse_iso_date(created_at),
        "expires_at": _parse_iso_date(closing_at) if closing_at else None,
        "category_name": category_name,
        "organization_name": organization_name,
        "organization_description": "",
        "organization_url": organization_url,
        "is_featured": False,
        "raw_data": item,
    }


async def import_reliefweb(
    limit: Optional[int] = None,
    dry_run: bool = False,
    use_ai: bool = False,
    batch_size: int = 20,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, int]:
    """
    Import remote jobs from ReliefWeb.

    Args:
        limit: Maximum number of jobs to import
        dry_run: If True, fetch but don't save to database
        use_ai: If True, use AI to enrich job descriptions
        batch_size: Number of concurrent AI requests (default: 20)
        progress_callback: Optional callback(completed, total) for progress updates

    Returns:
        Dict with keys: fetched, created, updated
    """
    app_name = settings.RELIEFWEB_APP_NAME
    headers = _reliefweb_headers(app_name)

    # Collect all job payloads first
    all_payloads = []
    offset = 0

    while True:
        list_items, total_count = _fetch_reliefweb_list(app_name, offset, headers)
        if not list_items:
            break

        for item in list_items:
            detail = _fetch_reliefweb_detail(str(item.get("id")), app_name, headers)
            if not detail:
                continue
            job_payload = _transform_reliefweb_item(detail)
            all_payloads.append(job_payload)
            if limit and len(all_payloads) >= limit:
                break

        offset += PAGE_SIZE
        if total_count is not None and offset >= total_count:
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
