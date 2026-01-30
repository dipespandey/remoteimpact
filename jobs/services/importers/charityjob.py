"""
CharityJob importer.

Scrapes remote jobs from https://www.charityjob.co.uk/jobs?workplace=remote
UK's leading charity and nonprofit job board with ~200+ remote positions.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from django.utils import timezone

from jobs.models import Job
from .common import batch_upsert_jobs, _map_job_type

logger = logging.getLogger(__name__)

BASE_URL = "https://www.charityjob.co.uk"
REMOTE_JOBS_URL = f"{BASE_URL}/jobs?workplace=remote"

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def _parse_salary_text(text: str) -> tuple[Optional[float], Optional[float], str]:
    """Parse salary like '32,000 per year' or '40,000 - 50,000'."""
    if not text:
        return None, None, "GBP"

    currency = "GBP"
    if "$" in text:
        currency = "USD"
    elif "\u20ac" in text or "EUR" in text.upper():
        currency = "EUR"

    amounts = re.findall(r"[\d,]+", text)
    amounts = [float(a.replace(",", "")) for a in amounts if float(a.replace(",", "")) >= 10000]

    if len(amounts) >= 2:
        return min(amounts), max(amounts), currency
    elif len(amounts) == 1:
        return amounts[0], amounts[0], currency
    return None, None, currency


def _extract_jobs_from_page(html: str) -> List[Dict]:
    """Extract job data from a CharityJob listing page."""
    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    # Find all job links with numeric IDs
    job_links = soup.find_all(
        "a",
        href=re.compile(r"charityjob\.co\.uk/jobs/.+/\d{5,}"),
    )

    seen_ids = set()
    for link in job_links:
        href = link.get("href", "")
        id_match = re.search(r"/(\d{5,})", href)
        if not id_match:
            continue

        job_id = id_match.group(1)
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)

        # Clean URL
        clean_url = re.sub(r"\?.*$", "", href)
        if not clean_url.startswith("http"):
            clean_url = urljoin(BASE_URL, clean_url)

        # Extract title from link text
        title = link.get_text(strip=True)
        if not title or len(title) < 3:
            continue

        # Walk up to find parent card
        card = link
        for _ in range(8):
            parent = card.parent
            if not parent:
                break
            card = parent
            card_text = card.get_text(" ", strip=True)
            if len(card_text) > 100:
                break

        card_text = card.get_text(" | ", strip=True)

        # Extract organization name
        org_name = "Unknown Organization"
        for a in card.find_all("a"):
            a_href = a.get("href", "")
            a_text = a.get_text(strip=True)
            if a_text and a_text != title and len(a_text) > 2:
                if any(x in a_href for x in ["/jobs?", "/volunteer", "/advice"]):
                    continue
                if a_text not in ["Apply", "Save", "Share", "Job Details"]:
                    org_name = a_text
                    break

        # Extract salary
        salary_min, salary_max, salary_currency = None, None, "GBP"
        salary_match = re.search(
            r"[\xa3$\u20ac]\s*[\d,]+(?:\s*[-\u2013]\s*[\xa3$\u20ac]?\s*[\d,]+)?(?:\s*per\s*\w+)?",
            card_text,
        )
        if salary_match:
            salary_min, salary_max, salary_currency = _parse_salary_text(
                salary_match.group(0)
            )

        # Extract job type
        job_type = "full-time"
        ct_lower = card_text.lower()
        if "part-time" in ct_lower or "part time" in ct_lower:
            job_type = "part-time"
        elif "contract" in ct_lower or "temporary" in ct_lower:
            job_type = "contract"
        elif "freelance" in ct_lower:
            job_type = "freelance"

        # Location
        location = "Remote"

        jobs.append(
            {
                "source": "charityjob",
                "external_id": job_id,
                "title": title,
                "description": "",
                "requirements": "",
                "location": location,
                "job_type": _map_job_type(job_type),
                "application_url": clean_url,
                "application_email": "",
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_currency": salary_currency,
                "posted_at": timezone.now(),
                "expires_at": None,
                "category_name": "Nonprofit & Charity",
                "organization_name": org_name,
                "organization_description": "",
                "organization_url": "",
                "is_featured": False,
                "raw_data": {
                    "source_url": clean_url,
                    "needs_crawling": True,
                },
            }
        )

    return jobs


def fetch_job_listings(
    max_pages: Optional[int] = None,
    delay: float = 1.5,
) -> List[Dict]:
    """Fetch all remote job listings from CharityJob."""
    all_jobs: List[Dict] = []
    page = 1

    while True:
        if max_pages and page > max_pages:
            break

        url = f"{REMOTE_JOBS_URL}&page={page}"
        logger.info("CharityJob: fetching page %d: %s", page, url)

        try:
            response = requests.get(url, headers=FETCH_HEADERS, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error("Failed to fetch CharityJob page %d: %s", page, e)
            break

        page_jobs = _extract_jobs_from_page(response.text)
        if not page_jobs:
            logger.info("No more jobs on page %d, stopping", page)
            break

        existing_ids = {j["external_id"] for j in all_jobs}
        new_jobs = [j for j in page_jobs if j["external_id"] not in existing_ids]
        all_jobs.extend(new_jobs)

        logger.info(
            "CharityJob page %d: %d new jobs (total: %d)",
            page, len(new_jobs), len(all_jobs),
        )

        total_pages_match = re.search(r'"total_pages":\s*(\d+)', response.text)
        if total_pages_match:
            total_pages = int(total_pages_match.group(1))
            if page >= total_pages:
                logger.info("Reached last page (%d/%d)", page, total_pages)
                break

        page += 1
        time.sleep(delay)

    return all_jobs


async def import_charityjob(
    limit: Optional[int] = None,
    max_pages: Optional[int] = None,
    dry_run: bool = False,
    use_ai: bool = False,
    batch_size: int = 20,
    delay: float = 1.5,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    provider: Optional[str] = None,
    skip_existing: bool = False,
) -> Dict[str, int]:
    """Import remote jobs from CharityJob."""
    all_payloads = fetch_job_listings(max_pages=max_pages, delay=delay)

    if limit:
        all_payloads = all_payloads[:limit]

    logger.info("CharityJob: fetched %d job listings", len(all_payloads))

    if dry_run:
        return {"fetched": len(all_payloads), "created": 0, "updated": 0}

    stats = await batch_upsert_jobs(
        all_payloads,
        use_ai=use_ai,
        batch_size=batch_size,
        progress_callback=progress_callback,
        provider=provider,
        skip_existing=skip_existing,
    )

    return stats
