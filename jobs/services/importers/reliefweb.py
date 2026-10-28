from __future__ import annotations

import logging
import re
from datetime import datetime, timezone as dt_timezone
from typing import Callable, Dict, Optional, Tuple

import requests
from django.conf import settings
from django.utils import timezone

from jobs.models import Job
from .common import batch_upsert_jobs

logger = logging.getLogger(__name__)

API_URL = "https://api.reliefweb.int/v2/jobs"
PAGE_SIZE = 100
REMOTE_QUERY = "NOT _exists_:country"


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
    try:
        response = requests.get(
            f"{API_URL}/{job_id}", params=params, headers=headers, timeout=30
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning("ReliefWeb job %s fetch failed: %s, skipping", job_id, e)
        return None
    data = response.json()
    items = data.get("data") or []
    return items[0] if items else None


# Map ReliefWeb categories to existing RemoteImpact domains
RELIEFWEB_CATEGORY_MAP = {
    # ReliefWeb career_categories → RemoteImpact domains
    "Program/Project Management": "Impact Careers",
    "Administration/Finance": "Nonprofit & Charity",
    "Advocacy/Communications": "Advocacy or Policy",
    "Donor Relations/Grants Management": "Nonprofit & Charity",
    "Logistics/Procurement": "Impact Careers",
    "Monitoring and Evaluation": "Impact Careers",
    "Information Management": "Technology & Engineering",
    "Information and Communications Technology": "Technology & Engineering",
    "Human Resources": "Impact Careers",
    "Operations & Administration": "Impact Careers",
    # ReliefWeb themes → RemoteImpact domains
    "Health": "Global Health",
    "Protection and Human Rights": "Human Rights & Justice",
    "Food and Nutrition": "Poverty & Economic Development",
    "Education": "Education & Research",
    "Climate Change and Environment": "Climate & Environment",
    "Disaster Management": "Humanitarian & Disaster Relief",
    "Shelter and Non-Food Items": "Humanitarian & Disaster Relief",
    "Water Sanitation Hygiene": "Global Health",
    "Camp Coordination / Management": "Humanitarian & Disaster Relief",
    "Agriculture": "Climate & Environment",
    "Gender": "Gender Equality and Social Inclusion (GESI)",
    "Recovery and Reconstruction": "Humanitarian & Disaster Relief",
    "Mine Action": "Humanitarian & Disaster Relief",
    "Peacekeeping and Peacebuilding": "Human Rights & Justice",
    "Coordination": "Impact Careers",
    "Safety and Security": "Impact Careers",
    "Contributions": "Nonprofit & Charity",
}


def _map_reliefweb_category(raw_name: str) -> str:
    """Map ReliefWeb category to existing RemoteImpact domain."""
    return RELIEFWEB_CATEGORY_MAP.get(raw_name, "Impact Careers")


def _has_remote_work_evidence(item: Dict) -> bool:
    """A missing country is not evidence that a ReliefWeb role is remote."""
    fields = item.get("fields") or {}
    title = str(fields.get("title") or "")
    title = re.sub(r"\bremote\s+sensing\b", "", title, flags=re.I)
    if re.search(r"\b(?:not|non)[-\s]+remote\b|\bhybrid\b", title, re.I):
        return False
    body = str(fields.get("body") or "")
    body = re.sub(r"(?i)</?(?:p|div|br|li|h[1-6])\b[^>]*>", "\n", body)
    body = re.sub(r"<[^>]*>", " ", body)
    if re.search(r"(?im)^\s*\**(?:location|work(?:ing)? (?:location|arrangement|mode)|duty station)\**\s*:\s*\**(?:not\s+remote|on[- ]site|in[- ]person|hybrid)\b", body):
        return False
    lines = [line.replace("*", "").strip() for line in body.splitlines()]
    values = []
    for line in lines:
        labeled = re.match(r"(?i)^(?:location|work(?:ing)? (?:location|arrangement|mode)|duty station)\s*:\s*(.{1,200})$", line)
        if labeled:
            values.append(labeled.group(1))
    if any(re.search(r"(?i)\b(?:not|non)[-\s]+remote\b|\bhybrid\b|\brequired attendance\b|\b(?:on[- ]site|in[- ]person)\b", value) for value in values):
        return False
    for value in values:
        if re.search(r"(?i)\b(?:remote|home[- ]based)\b", value) and not re.search(r"(?i)\bremote\s+sensing\b", value):
            return True
    for line in lines:
        if re.search(r"(?i)\b(?:this (?:is|position is|role is)|the (?:position|role) is)\s+(?:a\s+)?(?:fully\s+)?remote\s+(?:role|position|job)\b", line):
            return True
    return bool(re.search(r"\b(?:remote|home[- ]based)\b", title, re.I))


def _explicit_schedule(description: str) -> str:
    """Read explicit schedule labels without treating incidental mentions as hours."""
    text = re.sub(r"<[^>]+>", " ", description or "")
    found = set()
    for line in text.splitlines():
        line = line.replace("*", "").strip()
        match = re.match(r"(?i)^(?:commitment|working arrangements?|employment type|schedule|working hours|hours)\s*:\s*(.{1,200})$", line)
        if not match:
            continue
        value = match.group(1)
        if not re.match(r"(?i)^(?:flexible,?\s*)?(?:full|part)[ -]?time\b", value):
            continue
        if re.search(r"\b(?:not|no|optional)\b", value, re.I):
            continue
        if re.search(r"\bfull[ -]?time\b", value, re.I):
            found.add("full-time")
        if re.search(r"\bpart[ -]?time\b", value, re.I):
            found.add("part-time")
    return next(iter(found)) if len(found) == 1 else ""


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
        raw_name = first.get("name") or ""
        category_name = _map_reliefweb_category(raw_name)

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

    source_types = fields.get("type") or []
    if isinstance(source_types, dict):
        source_types = [source_types]
    labels = {str(entry.get("name") or "").strip().casefold() for entry in source_types if isinstance(entry, dict)}
    # A generic ReliefWeb job does not establish its working hours.
    is_volunteer = bool(re.search(r"\bvolunteer(?:ing)?\b", title, re.I))
    job_type = "contract" if labels == {"consultancy"} and not is_volunteer else _explicit_schedule(description)

    return {
        "source": Job.Source.RELIEFWEB,
        "external_id": str(item.get("id")),
        "title": title,
        "description": description,
        "requirements": description,
        "location": "Remote",
        "job_type": job_type,
        "application_url": application_url,
        "application_email": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
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
    provider: Optional[str] = None,
    skip_existing: bool = False,
) -> Dict[str, int]:
    """
    Import remote jobs from ReliefWeb.

    Args:
        limit: Maximum number of source records to examine
        dry_run: If True, fetch but don't save to database
        use_ai: If True, use AI to enrich job descriptions
        batch_size: Number of concurrent AI requests (default: 20)
        progress_callback: Optional callback(completed, total) for progress updates
        provider: DeepSeek, or None for the production default
        skip_existing: If True, skip jobs already imported (for incremental imports)

    Returns:
        Dict with keys: fetched, created, updated
    """
    app_name = settings.RELIEFWEB_APP_NAME
    headers = _reliefweb_headers(app_name)

    # Collect all job payloads first
    all_payloads = []
    offset = 0
    examined = 0

    while True:
        list_items, total_count = _fetch_reliefweb_list(app_name, offset, headers)
        if not list_items:
            break

        for item in list_items:
            if limit and examined >= limit:
                break
            examined += 1
            detail = _fetch_reliefweb_detail(str(item.get("id")), app_name, headers)
            if not detail:
                continue
            if not _has_remote_work_evidence(detail):
                continue
            job_payload = _transform_reliefweb_item(detail)
            all_payloads.append(job_payload)

        offset += PAGE_SIZE
        if total_count is not None and offset >= total_count:
            break
        if limit and examined >= limit:
            break

    if dry_run:
        return {"fetched": len(all_payloads), "created": 0, "updated": 0, "skip_retirement": True}

    # Batch upsert with optional AI processing
    stats = await batch_upsert_jobs(
        all_payloads,
        use_ai=use_ai,
        batch_size=batch_size,
        progress_callback=progress_callback,
        provider=provider,
        skip_existing=skip_existing,
    )
    # Filtered-out records may still exist upstream. Their lack of a sighting
    # must not be recorded as proof that ReliefWeb removed them.
    stats["skip_retirement"] = True
    return stats
