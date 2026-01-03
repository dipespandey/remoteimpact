"""
Probably Good job board importer.

Scrapes remote jobs from https://jobs.probablygood.org/?remote=remote
Webflow-based site with server-side rendering.
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
        application_url = job_url  # Default to Probably Good detail page
        external_links = card_elem.find_all("a", href=lambda x: x and x.startswith("http") and "probablygood.org" not in x)
        for link in external_links:
            href = link.get("href", "")
            if any(kw in href.lower() for kw in ["careers", "jobs", "workday", "greenhouse", "lever", "ashby", "apply"]):
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


def fetch_job_listings(
    max_pages: Optional[int] = None,
    delay: float = 1.0,
) -> List[Dict]:
    """
    Fetch all remote job listings from Probably Good.

    Args:
        max_pages: Maximum pages to fetch (None for all)
        delay: Delay between requests in seconds

    Returns:
        List of job payload dicts
    """
    jobs = []
    page = 1

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    while True:
        if max_pages and page > max_pages:
            break

        # Build URL with pagination
        if page == 1:
            url = REMOTE_JOBS_URL
        else:
            url = f"{REMOTE_JOBS_URL}&{PAGE_PARAM}={page}"

        logger.info(f"Fetching page {page}: {url}")

        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch page {page}: {e}")
            break

        soup = BeautifulSoup(response.text, "html.parser")

        # Find job cards - look for elements containing job posting links
        # The exact structure depends on Webflow's output
        job_links = soup.find_all("a", href=lambda x: x and "/job-postings/" in x)

        if not job_links:
            logger.info(f"No more jobs found on page {page}")
            break

        # Group links by their parent containers to get job cards
        seen_urls = set()
        page_jobs = []

        for link in job_links:
            href = link.get("href", "")
            if href in seen_urls:
                continue
            seen_urls.add(href)

            # Find the parent card element (walk up to find a reasonable container)
            card = link
            for _ in range(5):  # Walk up max 5 levels
                parent = card.parent
                if parent and parent.name in ["div", "article", "li", "section"]:
                    # Check if this parent contains more job-related content
                    parent_text = parent.get_text()
                    if "Added" in parent_text or "$" in parent_text:
                        card = parent
                        break
                    card = parent
                else:
                    break

            job_data = _extract_job_from_card(card, soup)
            if job_data and job_data["external_id"] not in [j["external_id"] for j in jobs]:
                page_jobs.append(job_data)

        jobs.extend(page_jobs)
        logger.info(f"Page {page}: Found {len(page_jobs)} jobs (total: {len(jobs)})")

        # Check for next page
        next_link = soup.find("a", href=lambda x: x and f"{PAGE_PARAM}={page + 1}" in x)
        if not next_link:
            logger.info(f"No next page link found, stopping at page {page}")
            break

        page += 1
        time.sleep(delay)

    return jobs


async def import_probablygood(
    limit: Optional[int] = None,
    max_pages: Optional[int] = None,
    dry_run: bool = False,
    use_ai: bool = False,
    fetch_descriptions: bool = True,
    batch_size: int = 20,
    delay: float = 1.0,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    provider: Optional[str] = None,
) -> Dict[str, int]:
    """
    Import remote jobs from Probably Good.

    Args:
        limit: Maximum number of jobs to import
        max_pages: Maximum pages to scrape (25 jobs/page)
        dry_run: If True, fetch but don't save to database
        use_ai: If True, use AI to enrich job descriptions
        fetch_descriptions: If True, fetch full descriptions from external URLs
        batch_size: Number of concurrent AI requests
        delay: Delay between page requests
        progress_callback: Optional callback(completed, total)
        provider: LLM provider ('deepseek', 'groq', 'mistral', or None for auto)

    Returns:
        Dict with keys: fetched, created, updated
    """
    # Fetch all job listings from Probably Good
    all_payloads = fetch_job_listings(max_pages=max_pages, delay=delay)

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
    )

    return stats
