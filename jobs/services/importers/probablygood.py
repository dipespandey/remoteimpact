"""
Probably Good job board importer.

Reads the public search index used by https://jobs.probablygood.org and keeps
only listings explicitly marked remote.
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
from ..crawlers.base import html_to_markdown

logger = logging.getLogger(__name__)

# Common headers for fetching external pages
FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE_URL = "https://jobs.probablygood.org"
REMOTE_JOBS_URL = f"{BASE_URL}/?remote=remote"
API_URL = "https://backend.jobs.probablygood.org/api/graphql/AlgoliaSearchKey"

# Pagination query param discovered from the site
PAGE_PARAM = "b74fbe7d_page"


def _parse_date_added(text: str) -> datetime:
    """
    Parse 'Added Dec 30' or 'Added 2 days ago' style dates.

    Returns datetime in UTC.
    """
    if not text:
        return timezone.now()

    text = text.lower().strip()

    # Remove "added" prefix
    text = re.sub(r"^added\s+", "", text)

    # Handle relative dates
    if "today" in text:
        return timezone.now()
    if "yesterday" in text:
        return timezone.now() - timedelta(days=1)

    days_match = re.search(r"(\d+)\s*days?\s*ago", text)
    if days_match:
        days = int(days_match.group(1))
        return timezone.now() - timedelta(days=days)

    # Handle absolute dates like "Dec 30" or "Jan 5"
    month_day_match = re.search(r"([a-z]{3})\s+(\d{1,2})", text)
    if month_day_match:
        month_str = month_day_match.group(1)
        day = int(month_day_match.group(2))

        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4,
            "may": 5, "jun": 6, "jul": 7, "aug": 8,
            "sep": 9, "oct": 10, "nov": 11, "dec": 12
        }
        month = months.get(month_str, 1)
        year = timezone.now().year

        # If the date is in the future, it's from last year
        try:
            parsed = datetime(year, month, day, tzinfo=dt_timezone.utc)
            if parsed > timezone.now():
                parsed = datetime(year - 1, month, day, tzinfo=dt_timezone.utc)
            return parsed
        except ValueError:
            return timezone.now()

    return timezone.now()


def _parse_salary(text: str) -> tuple[Optional[float], Optional[float], str]:
    """
    Parse salary text like '$55,000' or '$100,000 - $150,000'.

    Returns (min, max, currency).
    """
    if not text:
        return None, None, "USD"

    # Find all dollar amounts
    amounts = re.findall(r"\$?([\d,]+)", text)
    amounts = [float(a.replace(",", "")) for a in amounts if a]

    if len(amounts) >= 2:
        return min(amounts), max(amounts), "USD"
    elif len(amounts) == 1:
        return amounts[0], amounts[0], "USD"

    return None, None, "USD"


def _extract_job_from_card(card_elem, soup: BeautifulSoup) -> Optional[Dict]:
    """
    Extract job data from a job card element.

    The Webflow structure is:
    <div> (card)
      <a href="/job-postings/slug"></a>  (empty link)
      <div>  (content div)
        <div>  (inner div)
          <div></div>
          <div>  (title/org div)
            <h4>Job Title</h4>
            <a>Organization Name</a>
          </div>
          <div>AddedDec 30</div>
          <div>Location Type Level</div>
          ...
        </div>
      </div>
    </div>
    """
    try:
        # Find job URL from the job posting link
        job_link = card_elem.find("a", href=lambda x: x and "/job-postings/" in x)
        if not job_link:
            return None

        job_url = job_link.get("href", "")
        if job_url.startswith("/"):
            job_url = urljoin(BASE_URL, job_url)

        # Extract external ID from URL slug
        external_id = job_url.split("/job-postings/")[-1].strip("/")
        if not external_id:
            return None

        # Navigate to find title and org
        # The title is in an <h4> or <h5> tag
        title = ""
        title_elem = card_elem.find(["h4", "h5"])
        if title_elem:
            title = title_elem.get_text(strip=True)

        # Find organization name - look for links that aren't job posting links
        org_name = "Unknown Organization"
        for link in card_elem.find_all("a"):
            href = link.get("href", "")
            # Skip the job posting link and "Job Details" link
            if "/job-postings/" in href or not href:
                continue
            org_text = link.get_text(strip=True)
            # Skip common button texts
            if org_text and org_text not in ["Job Details", "Apply", ""] and len(org_text) > 2:
                org_name = org_text
                break

        # Get all text content from card for metadata extraction
        card_text = card_elem.get_text(" ", strip=True)

        # Extract job details from card text
        location = "Remote"
        job_type = "full-time"
        salary_min = None
        salary_max = None
        posted_at = timezone.now()

        # Look for location patterns
        location_patterns = [
            r"(Remote\s*[,\s]*[\w\s,]+(?:USA|UK|Canada|Germany|Australia|Netherlands|Uganda|India))",
            r"([\w\s]+,\s*(?:USA|UK|Canada|Germany|Australia|Netherlands|Uganda|India))",
            r"(Remote)",
        ]
        for pattern in location_patterns:
            match = re.search(pattern, card_text, re.IGNORECASE)
            if match:
                location = match.group(1).strip()
                break

        # Look for "Added" date
        added_match = re.search(r"Added\s*([A-Za-z]+\s+\d+|\d+\s*days?\s*ago|today|yesterday)", card_text, re.IGNORECASE)
        if added_match:
            posted_at = _parse_date_added(added_match.group(0))

        # Look for job type
        card_text_lower = card_text.lower()
        if "part-time" in card_text_lower or "part time" in card_text_lower:
            job_type = "part-time"
        elif "contract" in card_text_lower:
            job_type = "contract"
        elif "internship" in card_text_lower:
            job_type = "contract"  # Map internship to contract

        # Look for salary - numbers that look like salaries (5-6 digits)
        salary_match = re.search(r"(\d{2,3}),?(\d{3})", card_text)
        if salary_match:
            salary_str = salary_match.group(1) + salary_match.group(2)
            try:
                salary_val = float(salary_str)
                if 10000 <= salary_val <= 500000:  # Reasonable salary range
                    salary_min = salary_val
                    salary_max = salary_val
            except ValueError:
                pass

        # Find external application URL if present
        # Priority 1: Look for the "Job Details" button which has the actual application link
        application_url = job_url  # Default to Probably Good detail page
        
        job_details_button = card_elem.find("a", class_=lambda c: c and "job-details-button" in c)
        if job_details_button:
            href = job_details_button.get("href", "")
            if href and href.startswith("http") and "probablygood.org" not in href:
                application_url = href
        
        # Priority 2: Fall back to scanning for any external career-related links
        if application_url == job_url:
            external_links = card_elem.find_all("a", href=lambda x: x and x.startswith("http") and "probablygood.org" not in x)
            for link in external_links:
                href = link.get("href", "")
                # Accept any external link as a potential application URL
                # (most Probably Good listings link directly to the employer's job page)
                if href:
                    application_url = href
                    break

        return {
            "source": Job.Source.PROBABLYGOOD,
            "external_id": external_id,
            "title": title,
            "description": "",  # Will be filled from detail page or AI
            "requirements": "",
            "location": location,
            "job_type": job_type,
            "application_url": application_url,
            "application_email": "",
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "USD",
            "posted_at": posted_at,
            "expires_at": None,
            "category_name": "Impact Careers",
            "organization_name": org_name,
            "organization_description": "",
            "organization_url": "",
            "is_featured": False,
            "raw_data": {
                "probablygood_url": job_url,
                "needs_crawling": True,  # Mark for later detail crawling
            },
        }

    except Exception as e:
        logger.warning(f"Failed to parse job card: {e}")
        return None


def _fetch_greenhouse_description(url: str) -> str:
    """Fetch job description from Greenhouse API."""
    from ..crawlers.greenhouse import extract_greenhouse_info, fetch_greenhouse_job, parse_greenhouse_job

    company, job_id = extract_greenhouse_info(url)
    if not company or not job_id:
        return ""

    data = fetch_greenhouse_job(company, job_id)
    if not data:
        return ""

    parsed = parse_greenhouse_job(data)
    return parsed.get("description", "")


def _fetch_lever_description(url: str) -> str:
    """Fetch job description from Lever API."""
    from ..crawlers.lever import extract_lever_info, fetch_lever_job, parse_lever_job

    company, job_id = extract_lever_info(url)
    if not company or not job_id:
        return ""

    data = fetch_lever_job(company, job_id)
    if not data:
        return ""

    parsed = parse_lever_job(data)
    desc = parsed.get("description", "")
    if parsed.get("requirements"):
        desc += "\n\n## Requirements\n" + parsed["requirements"]
    return desc


def _fetch_ashby_description(url: str) -> str:
    """Fetch job description from Ashby API."""
    from ..crawlers.ashby import extract_ashby_info, fetch_ashby_job, parse_ashby_job

    company, job_id = extract_ashby_info(url)
    if not company or not job_id:
        return ""

    data = fetch_ashby_job(company, job_id)
    if not data:
        return ""

    parsed = parse_ashby_job(data)
    return parsed.get("description", "")


def fetch_job_description(url: str, timeout: int = 30) -> str:
    """
    Fetch a job page and extract the main content as text.

    For known platforms (Greenhouse, Lever, Ashby), uses their APIs.
    For other sites, attempts to scrape the page content.

    Args:
        url: The job posting URL
        timeout: Request timeout in seconds

    Returns:
        Extracted job description text, or empty string if failed
    """
    if not url or "probablygood.org" in url:
        return ""

    # Use API-based fetching for known platforms
    try:
        if "greenhouse.io" in url:
            desc = _fetch_greenhouse_description(url)
            if desc:
                return desc

        if "lever.co" in url:
            desc = _fetch_lever_description(url)
            if desc:
                return desc

        if "ashbyhq.com" in url:
            desc = _fetch_ashby_description(url)
            if desc:
                return desc
    except Exception as e:
        logger.warning(f"API fetch failed for {url}: {e}")

    # For other sites, try basic HTML scraping
    # Note: Many modern sites use JavaScript rendering and won't work
    try:
        response = requests.get(url, headers=FETCH_HEADERS, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return ""

    soup = BeautifulSoup(response.text, "html.parser")

    # Check if page requires JavaScript
    body_text = soup.find("body")
    if body_text:
        body_content = body_text.get_text(strip=True)
        if "enable JavaScript" in body_content or len(body_content) < 100:
            logger.debug(f"Page requires JavaScript: {url}")
            return ""

    # Remove script, style, nav, header, footer elements
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    # Try to find the main job content using common selectors
    content = None

    # Common job description container selectors
    selectors = [
        # Workday
        {"data-automation-id": "jobPostingDescription"},
        {"class_": re.compile(r"job[-_]?description", re.I)},
        {"class_": re.compile(r"posting[-_]?description", re.I)},
        # Generic
        {"role": "main"},
        {"id": "main"},
        {"class_": "main"},
        {"tag": "main"},
        {"tag": "article"},
    ]

    for selector in selectors:
        if "tag" in selector:
            content = soup.find(selector["tag"])
        else:
            content = soup.find(attrs=selector)
        if content:
            break

    # Fallback to body if no specific container found
    if not content:
        content = soup.find("body")

    if not content:
        return ""

    # Convert to markdown-style text
    html_content = str(content)
    text = html_to_markdown(html_content)

    # Clean up: remove excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    # Limit to reasonable length (first ~8000 chars for AI processing)
    if len(text) > 8000:
        text = text[:8000] + "..."

    return text


def fetch_descriptions_batch(
    payloads: List[Dict],
    delay: float = 0.5,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[Dict]:
    """
    Fetch job descriptions for a batch of job payloads.

    Args:
        payloads: List of job payload dicts with application_url
        delay: Delay between requests in seconds
        progress_callback: Optional callback(completed, total)

    Returns:
        Updated payloads with descriptions filled in
    """
    total = len(payloads)
    updated_payloads = []

    for i, payload in enumerate(payloads):
        url = payload.get("application_url", "")

        if url and "probablygood.org" not in url:
            logger.debug(f"Fetching description from: {url}")
            description = fetch_job_description(url)

            if description:
                payload = payload.copy()
                payload["description"] = description
                logger.debug(f"  Got {len(description)} chars for '{payload.get('title', 'Unknown')}'")
            else:
                logger.debug(f"  No description extracted for '{payload.get('title', 'Unknown')}'")

        updated_payloads.append(payload)

        if progress_callback:
            progress_callback(i + 1, total)

        # Rate limiting between requests
        if i < total - 1:
            time.sleep(delay)

    return updated_payloads


def _get_algolia_config() -> Dict[str, str]:
    query = """query AlgoliaSearchKey {
  algolia_search_key {
    api_key
    app_id
    index_name
    index_name_sorted_by_votes
    index_name_profiles
    index_name_jobs
    index_name_jobs_sorted_by_closes_at
    jobs_object_id_field
    __typename
  }
}"""
    response = requests.post(
        API_URL,
        json={
            "operationName": "AlgoliaSearchKey",
            "variables": {},
            "query": query,
        },
        headers=FETCH_HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    config = (payload.get("data") or {}).get("algolia_search_key") or {}
    required = {"api_key", "app_id", "index_name_jobs"}
    if not required.issubset(config):
        raise ValueError("Probably Good search configuration is unavailable")
    return config


def _parse_algolia_salary(text: str) -> tuple[Optional[float], Optional[float], str]:
    """Parse annual salary text without treating hourly rates as annual pay."""
    text = str(text or "").strip()
    lowered = text.lower()
    if not text or any(unit in lowered for unit in ("/hour", "per hour", "hourly")):
        return None, None, ""

    currency = "USD"
    if "c$" in lowered or "cad" in lowered:
        currency = "CAD"
    elif "a$" in lowered or "aud" in lowered:
        currency = "AUD"
    elif "£" in text or "gbp" in lowered:
        currency = "GBP"
    elif "€" in text or "eur" in lowered:
        currency = "EUR"
    elif "₹" in text or "inr" in lowered:
        currency = "INR"

    amounts = []
    for number, thousands in re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*([kK]?)", text):
        value = float(number.replace(",", ""))
        if thousands:
            value *= 1000
        if 10000 <= value <= 2_000_000:
            amounts.append(value)
    if not amounts:
        return None, None, ""
    return min(amounts), max(amounts) if len(amounts) > 1 else None, currency


def _parse_algolia_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _tag_names(hit: Dict, key: str) -> List[str]:
    return [
        str(tag.get("name", "")).strip()
        for tag in hit.get(key, [])
        if isinstance(tag, dict) and str(tag.get("name", "")).strip()
    ]


def _skill_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9+#.]+", "-", value.lower()).strip("-")


def _extract_job_from_algolia_hit(hit: Dict) -> Optional[Dict]:
    remote_locations = [
        location
        for location in hit.get("locations", [])
        if isinstance(location, dict)
        and (location.get("is_remote") or location.get("type") == "REMOTE")
    ]
    title = str(hit.get("title") or "").strip()
    description = str(hit.get("description") or "").strip()
    organization = hit.get("org") or {}
    external_id = str(hit.get("id") or hit.get("objectID") or "").strip()
    if (
        not remote_locations
        or not title
        or not description
        or not external_id
        or not organization.get("name")
    ):
        return None

    slug = str(hit.get("slug") or hit.get("objectID") or external_id).strip()
    detail_url = f"{BASE_URL}/jobs/{slug}"
    salary_min, salary_max, salary_currency = _parse_algolia_salary(
        hit.get("salary_text", "")
    )
    workload = _tag_names(hit, "tags_workload")
    area_tags = _tag_names(hit, "tags_area")
    skill_names = _tag_names(hit, "tags_skill")
    location = "; ".join(
        dict.fromkeys(str(item.get("name") or item.get("remote_name") or "Remote") for item in remote_locations)
    )
    expires_at = _parse_algolia_datetime(hit.get("closes_at"))
    if expires_at and expires_at <= timezone.now():
        return None

    return {
        "source": Job.Source.PROBABLYGOOD,
        "external_id": external_id,
        "title": title,
        "description": description,
        "requirements": "",
        "location": location or "Remote",
        "job_type": _map_job_type(workload[0] if workload else "full-time"),
        "application_url": str(hit.get("url_external") or detail_url).strip(),
        "application_email": "",
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "posted_at": _parse_algolia_datetime(hit.get("published_at")) or timezone.now(),
        "expires_at": expires_at,
        "category_name": area_tags[0] if area_tags else "Other Impact Areas",
        "organization_name": str(organization.get("name") or "").strip(),
        "organization_description": str(organization.get("description") or "").strip(),
        "organization_url": str(organization.get("website") or "").strip(),
        "skills": [slug for slug in (_skill_slug(name) for name in skill_names) if slug],
        "is_featured": bool(organization.get("is_highlighted")),
        "raw_data": {
            "probablygood_url": detail_url,
            "salary_text": hit.get("salary_text") or "",
            "source_ext": hit.get("source_ext"),
            "locations": remote_locations,
            "tags_area": area_tags,
            "tags_skill": skill_names,
            "tags_experience": _tag_names(hit, "tags_experience"),
            "tags_education": _tag_names(hit, "tags_education"),
            "needs_crawling": False,
        },
    }


def fetch_job_listings(
    max_pages: Optional[int] = 60,
    delay: float = 1.0,
    limit: Optional[int] = None,
) -> List[Dict]:
    """Fetch newest remote listings from Probably Good's public search index."""
    config = _get_algolia_config()
    app_id = config["app_id"]
    search_url = f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/*/queries"
    headers = {
        "Content-Type": "application/json",
        "X-Algolia-Application-Id": app_id,
        "X-Algolia-API-Key": config["api_key"],
    }
    jobs = []
    page = 0
    page_size = 100
    logger.info("Starting Probably Good Algolia fetch (max_pages=%s)", max_pages)

    while max_pages is None or page < max_pages:
        response = requests.post(
            search_url,
            json={
                "requests": [
                    {
                        "indexName": config["index_name_jobs"],
                        "query": "",
                        "hitsPerPage": page_size,
                        "page": page,
                    }
                ]
            },
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()["results"][0]
        hits = result.get("hits", [])
        if not hits:
            break
        for hit in hits:
            payload = _extract_job_from_algolia_hit(hit)
            if payload:
                jobs.append(payload)
                if limit and len(jobs) >= limit:
                    return jobs[:limit]
        page += 1
        if page >= result.get("nbPages", 0):
            break
        if delay:
            time.sleep(min(delay, 1.0))

    return jobs


async def import_probablygood(
    limit: Optional[int] = None,
    max_pages: Optional[int] = None,
    dry_run: bool = False,
    use_ai: bool = False,
    fetch_descriptions: bool = False,
    batch_size: int = 20,
    delay: float = 1.0,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    provider: Optional[str] = None,
    skip_existing: bool = False,
) -> Dict[str, int]:
    """
    Import remote jobs from Probably Good.

    Args:
        limit: Maximum number of jobs to import
        max_pages: Maximum Algolia pages to fetch (100 jobs/page). Default: 60 (the whole feed).
        dry_run: If True, fetch but don't save to database
        use_ai: If True, use AI to enrich job descriptions
        fetch_descriptions: Re-fetch descriptions from employer URLs. Normally unnecessary because the feed includes them.
        batch_size: Number of concurrent AI requests
        delay: Delay between page requests
        progress_callback: Optional callback(completed, total)
        provider: DeepSeek, or None for the production default
        skip_existing: If True, skip jobs already imported (for incremental imports)

    Returns:
        Dict with keys: fetched, created, updated
    """
    # Always fetch the whole feed: existing listings are skipped cheaply, and a complete
    # fetch is what lets retire_unseen_listings take down roles the feed has dropped.
    if max_pages is None:
        max_pages = 60

    # Fetch all job listings from Probably Good
    all_payloads = fetch_job_listings(max_pages=max_pages, delay=delay, limit=limit)

    if limit:
        all_payloads = all_payloads[:limit]

    logger.info(f"Fetched {len(all_payloads)} job listings from Probably Good")

    # Fetch full descriptions from external URLs if requested
    # This is required for AI processing to work effectively
    if fetch_descriptions and (use_ai or not dry_run):
        logger.info(f"Fetching job descriptions from {len(all_payloads)} external URLs...")

        def desc_progress(completed: int, total: int):
            if completed % 10 == 0 or completed == total:
                logger.info(f"  Fetched descriptions: {completed}/{total}")

        all_payloads = fetch_descriptions_batch(
            all_payloads,
            delay=0.5,
            progress_callback=desc_progress,
        )

        # Count how many descriptions we got
        with_desc = sum(1 for p in all_payloads if p.get("description"))
        logger.info(f"  Got descriptions for {with_desc}/{len(all_payloads)} jobs")

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
