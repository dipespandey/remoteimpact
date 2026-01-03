from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone
from typing import Callable, Dict, List, Optional

import requests
from django.conf import settings
from django.utils import timezone

from jobs.models import Job
from .common import _algolia_headers, _map_job_type, batch_upsert_jobs

logger = logging.getLogger(__name__)


def _build_climatebase_payload() -> Dict:
    return {
        "query": "",
        "hitsPerPage": 1000,
        "clickAnalytics": True,
        "filters": 'remote_preferences:"Remote"',
        "facetFilters": [
            "active:true",
            "employer_has_approval:true",
            ["id:-29340874"],
            ["id:-46605467"],
            ["id:-47385433"],
            ["id:-47385435"],
        ],
        "attributesToRetrieve": ["*"],
        "attributesToHighlight": [],
        "analytics": False,
        "getRankingInfo": False,
        "enablePersonalization": False,
        "enableABTest": False,
    }


def _browse_climatebase(app_id: str, headers: Dict[str, str], base_payload: Dict):
    url = f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/Job_production/browse"
    payload = dict(base_payload)
    while True:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        hits = data.get("hits") or []
        if not hits:
            break
        yield hits
        cursor = data.get("cursor")
        if not cursor:
            break
        payload = {"cursor": cursor}


def _parse_date(value: Optional[object]) -> datetime:
    if value is None:
        return timezone.now()
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10**12:
            timestamp = timestamp / 1000.0
        return datetime.fromtimestamp(timestamp, tz=dt_timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:  # pragma: no cover - defensive
        logger.warning("Could not parse Climatebase date %s", value)
        return timezone.now()


def _pick_application_url(hit: Dict) -> str:
    # Check common URL fields first
    for key in (
        "application_url",
        "apply_url",
        "apply_link",
        "apply",
        "job_url",
        "url",
        "external_url",
        "source_url",
        "link",
    ):
        if hit.get(key):
            return hit[key]

    # Check if how_to_apply contains a URL
    how_to_apply = hit.get("how_to_apply", "")
    if how_to_apply and how_to_apply.startswith("http"):
        return how_to_apply

    # Fallback: construct Climatebase job page URL from ID
    job_id = hit.get("id") or hit.get("objectID")
    if job_id:
        return f"https://climatebase.org/job/{job_id}"

    return ""


def _transform_climatebase_hit(hit: Dict) -> Optional[Dict]:
    remote_pref_value = hit.get("remote_preferences")
    if isinstance(remote_pref_value, list):
        remote_pref = " ".join([str(v) for v in remote_pref_value if v]).lower()
    elif remote_pref_value is None:
        remote_pref = ""
    else:
        remote_pref = str(remote_pref_value).lower()
    if remote_pref and "remote" not in remote_pref:
        return None

    locations: List[str] = []
    raw_locations = hit.get("locations")
    if isinstance(raw_locations, list):
        locations = [loc for loc in raw_locations if loc]
    elif isinstance(raw_locations, str):
        locations = [raw_locations]
    location = locations[0] if locations else "Remote"
    if "remote" in remote_pref:
        location = "Remote"

    sectors = hit.get("sectors") or hit.get("tags") or []
    if isinstance(sectors, str):
        sectors = [sectors]
    category_name = sectors[0] if sectors else "Impact Careers"

    job_types = hit.get("job_types") or []
    if isinstance(job_types, str):
        job_types = [job_types]
    job_type_label = job_types[0] if job_types else ""

    organization_name = (
        hit.get("employer_name")
        or hit.get("name_of_employer")
        or "Unknown Organization"
    )
    organization_description = hit.get("employer_short_description", "")

    salary_min = hit.get("salary_from") or hit.get("salary_min")
    salary_max = hit.get("salary_to") or hit.get("salary_max")
    salary_currency = (hit.get("salary_currency") or "USD")[:3]

    posted_at = _parse_date(
        hit.get("activation_date")
        or hit.get("created_at")
        or hit.get("updated_at")
        or hit.get("posted_at")
    )

    description = (
        hit.get("description_html")
        or hit.get("description")
        or hit.get("description_text")
        or hit.get("description_plain")
        or hit.get("description_plaintext")
        or hit.get("description_plain_text")
        or ""
    )

    # Extract application email from how_to_apply if it's an email
    how_to_apply = hit.get("how_to_apply", "")
    application_email = ""
    if how_to_apply and "@" in how_to_apply and not how_to_apply.startswith("http"):
        application_email = how_to_apply.strip()

    return {
        "source": Job.Source.CLIMATEBASE,
        "external_id": str(hit.get("id")),
        "title": hit.get("title", "Untitled role"),
        "description": description,
        "requirements": description,
        "location": location,
        "job_type": _map_job_type(job_type_label),
        "application_url": _pick_application_url(hit),
        "application_email": application_email,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "posted_at": posted_at,
        "expires_at": None,
        "category_name": category_name,
        "organization_name": organization_name,
        "organization_description": organization_description,
        "organization_url": "",
        "is_featured": bool(hit.get("featured")),
        "raw_data": hit,
    }


async def import_climatebase(
    limit: Optional[int] = None,
    dry_run: bool = False,
    use_ai: bool = False,
    batch_size: int = 20,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    provider: Optional[str] = None,
    skip_existing: bool = False,
) -> Dict[str, int]:
    """
    Import remote jobs from Climatebase.

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
    app_id = settings.CLIMATEBASE_ALGOLIA_APP_ID
    api_key = settings.CLIMATEBASE_ALGOLIA_API_KEY
    headers = _algolia_headers(app_id, api_key)
    headers["X-Algolia-Agent"] = (
        "Algolia for JavaScript (4.24.0); Browser; RemoteImpactImporter"
    )
    base_payload = _build_climatebase_payload()

    # Collect all job payloads first
    all_payloads = []
    for hits in _browse_climatebase(app_id, headers, base_payload):
        for hit in hits:
            job_payload = _transform_climatebase_hit(hit)
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
