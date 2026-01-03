"""
Google Search importer for job board URLs.

This module searches Google for job listings on Greenhouse, Lever, and Ashby
job boards and extracts the URLs for later crawling.

Supports multiple search backends:
- Google Custom Search API: 100 free queries/day, official Google results
- DuckDuckGo (ddgs): Free, limited results
- Serper.dev: 2,500 free searches/month
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import requests
from django.conf import settings

from .common import batch_upsert_jobs

logger = logging.getLogger(__name__)

# Search queries for each job board
# Note: If using a CSE restricted to job board sites, use UNIFIED_SEARCH_QUERY instead
SEARCH_QUERIES = {
    "greenhouse": 'site:boards.greenhouse.io "non-profit" remote',
    "lever": 'site:jobs.lever.co "non-profit" remote',
    "ashby": 'site:jobs.ashbyhq.com "non-profit" remote',
}

# Unified query for CSE that's already restricted to job board sites
# This is more efficient - searches all sites with one query
UNIFIED_SEARCH_QUERY = '"non-profit" remote'

# Source mapping for the Job model
SOURCE_MAPPING = {
    "greenhouse": "greenhouse",
    "lever": "lever",
    "ashby": "ashby",
}


def _extract_job_id_from_url(url: str, board_type: str) -> str:
    """
    Extract a unique job identifier from the URL.
    Falls back to URL hash if pattern doesn't match.
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")

    if board_type == "greenhouse":
        # Pattern: boards.greenhouse.io/company/jobs/123456
        match = re.search(r"/jobs/(\d+)", path)
        if match:
            return match.group(1)

    elif board_type == "lever":
        # Pattern: jobs.lever.co/company/uuid
        parts = path.split("/")
        if len(parts) >= 2:
            return parts[-1]

    elif board_type == "ashby":
        # Pattern: jobs.ashbyhq.com/company/uuid
        parts = path.split("/")
        if len(parts) >= 2:
            return parts[-1]

    # Fallback: use URL hash
    return hashlib.md5(url.encode()).hexdigest()[:16]


def _extract_company_from_url(url: str, board_type: str) -> str:
    """Extract company name/slug from the URL."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")

    if parts:
        return parts[0].replace("-", " ").title()

    return "Unknown Organization"


def _is_valid_job_url(url: str, board_type: str) -> bool:
    """Check if URL looks like a valid job posting URL."""
    parsed = urlparse(url)

    if board_type == "greenhouse":
        # Valid: boards.greenhouse.io/company/jobs/123
        return "/jobs/" in parsed.path and parsed.netloc == "boards.greenhouse.io"

    elif board_type == "lever":
        # Valid: jobs.lever.co/company/uuid
        parts = parsed.path.strip("/").split("/")
        return len(parts) >= 2 and parsed.netloc == "jobs.lever.co"

    elif board_type == "ashby":
        # Valid: jobs.ashbyhq.com/company/uuid
        parts = parsed.path.strip("/").split("/")
        return len(parts) >= 2 and parsed.netloc == "jobs.ashbyhq.com"

    return False


def search_google_cse(
    query: str,
    num_results: int = 100,
    use_date_binning: bool = True,
) -> List[str]:
    """
    Search using Google Custom Search API with date binning for more results.

    Free tier: 100 queries/day
    Each query returns max 10 results, pagination up to 100 results.

    Date binning strategy:
    - Split search into date ranges (last week, last month, last 3 months, older)
    - This helps get more diverse results within query limits

    Args:
        query: The search query
        num_results: Target number of results (will use multiple queries if needed)
        use_date_binning: Use date ranges to get more diverse results

    Returns:
        List of unique URLs from search results
    """
    api_key = getattr(settings, "GOOGLE_CSE_API_KEY", None)
    cx = getattr(settings, "GOOGLE_CSE_CX", None)

    if not api_key or not cx:
        logger.error(
            "GOOGLE_CSE_API_KEY or GOOGLE_CSE_CX not set. "
            "Configure in settings.py"
        )
        return []

    # Date bins: (label, dateRestrict parameter)
    # dateRestrict: d[number]=days, w[number]=weeks, m[number]=months, y[number]=years
    # Note: bins overlap (month includes week, etc.) but help surface different results
    date_bins = [
        ("last_week", "w1"),
        ("last_month", "m1"),
        ("last_3_months", "m3"),
        ("all_time", None),  # Keep all_time to catch older indexed pages
    ] if use_date_binning else [("all_time", None)]

    all_urls = set()
    queries_used = 0

    for date_label, date_restrict in date_bins:
        if len(all_urls) >= num_results:
            break

        # Paginate through results (start: 1, 11, 21, ... up to 91)
        for start in range(1, 92, 10):  # Max 10 pages of 10 results
            if len(all_urls) >= num_results:
                break

            try:
                params = {
                    "key": api_key,
                    "cx": cx,
                    "q": query,
                    "start": start,
                    "num": 10,  # Max per request
                }
                if date_restrict:
                    params["dateRestrict"] = date_restrict

                logger.info(f"Google CSE: {query} (start={start}, date={date_label})")
                response = requests.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params=params,
                    timeout=30,
                )
                queries_used += 1

                if response.status_code == 429:
                    logger.warning("Google CSE rate limit reached")
                    break

                response.raise_for_status()
                data = response.json()

                items = data.get("items", [])
                if not items:
                    logger.debug(f"No more results for date={date_label}, start={start}")
                    break

                for item in items:
                    url = item.get("link")
                    if url:
                        all_urls.add(url)
                        logger.debug(f"Found: {url}")

                # Check if there are more results
                total_results = int(data.get("searchInformation", {}).get("totalResults", 0))
                if start + 10 > total_results:
                    break

            except requests.RequestException as e:
                logger.error(f"Google CSE request failed: {e}")
                break
            except Exception as e:
                logger.error(f"Google CSE error: {e}")
                break

    logger.info(f"Google CSE: {len(all_urls)} unique URLs found using {queries_used} queries")
    return list(all_urls)


def search_duckduckgo(query: str, num_results: int = 100) -> List[str]:
    """
    Search using DuckDuckGo via the ddgs library.

    This is free and reliable - recommended for most use cases.

    Args:
        query: The search query
        num_results: Maximum number of results

    Returns:
        List of URLs from search results
    """
    try:
        from ddgs import DDGS
    except ImportError:
        logger.error("ddgs not installed. Run: uv add ddgs")
        return []

    urls = []
    try:
        logger.info(f"Searching DuckDuckGo: {query}")
        results = DDGS().text(query, max_results=num_results)

        for result in results:
            url = result.get("href")
            if url:
                urls.append(url)
                logger.debug(f"Found URL: {url}")

    except Exception as e:
        logger.error(f"DuckDuckGo search failed: {e}")

    return urls


def search_serper(query: str, num_results: int = 100) -> List[str]:
    """
    Search Google via Serper.dev API.

    Serper.dev offers 2,500 free searches/month.
    Get your API key at: https://serper.dev

    Args:
        query: The search query
        num_results: Maximum number of results (max 100 per request)

    Returns:
        List of URLs from search results
    """
    api_key = getattr(settings, "SERPER_API_KEY", None)
    if not api_key:
        logger.error(
            "SERPER_API_KEY not set. Get a free key at https://serper.dev "
            "and add it to settings.py"
        )
        return []

    urls = []
    try:
        logger.info(f"Searching via Serper: {query}")
        response = requests.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
            json={
                "q": query,
                "num": min(num_results, 100),  # Serper max is 100
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        for result in data.get("organic", []):
            url = result.get("link")
            if url:
                urls.append(url)
                logger.debug(f"Found URL: {url}")

    except requests.RequestException as e:
        logger.error(f"Serper API request failed: {e}")
    except Exception as e:
        logger.error(f"Serper search failed: {e}")

    return urls


def search_google_free(query: str, num_results: int = 100, delay: float = 2.0) -> List[str]:
    """
    Search Google using googlesearch-python (free but unreliable).

    Note: This method is often blocked by Google. Use Serper.dev for reliability.

    Args:
        query: The search query
        num_results: Maximum number of results to fetch
        delay: Delay between requests to avoid rate limiting

    Returns:
        List of URLs from search results
    """
    try:
        from googlesearch import search
    except ImportError:
        logger.error("googlesearch-python not installed. Run: uv add googlesearch-python")
        return []

    urls = []
    try:
        logger.info(f"Searching Google (free): {query}")
        for url in search(query, num_results=num_results, sleep_interval=delay):
            urls.append(url)
            logger.debug(f"Found URL: {url}")
    except Exception as e:
        logger.error(f"Google search failed (likely rate limited): {e}")

    return urls


def _detect_board_type(url: str) -> Optional[str]:
    """Detect which job board a URL belongs to."""
    if "boards.greenhouse.io" in url:
        return "greenhouse"
    elif "jobs.lever.co" in url:
        return "lever"
    elif "jobs.ashbyhq.com" in url:
        return "ashby"
    return None


def search_google_cse_unified(
    query: str = UNIFIED_SEARCH_QUERY,
    num_results: int = 200,
    use_date_binning: bool = True,
) -> List[Dict[str, Any]]:
    """
    Search using Google CSE with a unified query (for CSEs restricted to job sites).

    This is more efficient than per-site queries when your CSE is already
    configured to only search job board sites.

    Args:
        query: Search query (default: "non-profit" remote)
        num_results: Target number of results
        use_date_binning: Use date ranges to get more results

    Returns:
        List of job payload dicts ready for upsert
    """
    api_key = getattr(settings, "GOOGLE_CSE_API_KEY", None)
    cx = getattr(settings, "GOOGLE_CSE_CX", None)

    if not api_key or not cx:
        logger.error("GOOGLE_CSE_API_KEY or GOOGLE_CSE_CX not set")
        return []

    date_bins = [
        ("last_week", "w1"),
        ("last_month", "m1"),
        ("last_3_months", "m3"),
        ("all_time", None),
    ] if use_date_binning else [("all_time", None)]

    all_urls = {}  # url -> raw item data
    queries_used = 0

    for date_label, date_restrict in date_bins:
        if len(all_urls) >= num_results:
            break

        for start in range(1, 92, 10):
            if len(all_urls) >= num_results:
                break

            try:
                params = {
                    "key": api_key,
                    "cx": cx,
                    "q": query,
                    "start": start,
                    "num": 10,
                }
                if date_restrict:
                    params["dateRestrict"] = date_restrict

                logger.info(f"Google CSE unified: {query} (start={start}, date={date_label})")
                response = requests.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params=params,
                    timeout=30,
                )
                queries_used += 1

                if response.status_code == 429:
                    logger.warning("Google CSE rate limit reached")
                    break

                response.raise_for_status()
                data = response.json()

                items = data.get("items", [])
                if not items:
                    break

                for item in items:
                    url = item.get("link")
                    if url and url not in all_urls:
                        all_urls[url] = item

                total_results = int(data.get("searchInformation", {}).get("totalResults", 0))
                if start + 10 > total_results:
                    break

            except Exception as e:
                logger.error(f"Google CSE error: {e}")
                break

    logger.info(f"Google CSE unified: {len(all_urls)} unique URLs using {queries_used} queries")

    # Convert to payloads, detecting board type from URL
    payloads = []
    for url, item in all_urls.items():
        board_type = _detect_board_type(url)
        if not board_type:
            logger.debug(f"Skipping unknown board URL: {url}")
            continue

        if not _is_valid_job_url(url, board_type):
            logger.debug(f"Skipping invalid job URL: {url}")
            continue

        job_id = _extract_job_id_from_url(url, board_type)
        company_name = _extract_company_from_url(url, board_type)

        payload = {
            "source": SOURCE_MAPPING[board_type],
            "external_id": job_id,
            "title": item.get("title", f"Job at {company_name}"),
            "organization_name": company_name,
            "organization_url": "",
            "description": item.get("snippet", ""),
            "requirements": "",
            "location": "Remote",
            "job_type": "full-time",
            "application_url": url,
            "raw_data": {
                "source_url": url,
                "board_type": board_type,
                "google_title": item.get("title"),
                "google_snippet": item.get("snippet"),
                "needs_crawling": True,
            },
        }
        payloads.append(payload)

    return payloads


def search_google(
    query: str,
    num_results: int = 100,
    delay: float = 2.0,
    backend: str = "auto",
    use_date_binning: bool = True,
) -> List[str]:
    """
    Search and return list of URLs.

    Args:
        query: The search query
        num_results: Maximum number of results to fetch
        delay: Delay between requests (only for googlesearch backend)
        backend: 'google_cse', 'serper', 'duckduckgo', 'googlesearch', or 'auto'
        use_date_binning: Use date ranges to get more results (google_cse only)

    Returns:
        List of URLs from search results
    """
    if backend == "auto":
        # Priority: Google CSE > Serper > DuckDuckGo
        if getattr(settings, "GOOGLE_CSE_API_KEY", None) and getattr(settings, "GOOGLE_CSE_CX", None):
            backend = "google_cse"
            logger.info("Using Google Custom Search API")
        elif getattr(settings, "SERPER_API_KEY", None):
            backend = "serper"
            logger.info("Using Serper backend")
        else:
            backend = "duckduckgo"
            logger.warning("Using DuckDuckGo (limited results)")

    if backend == "google_cse":
        return search_google_cse(query, num_results=num_results, use_date_binning=use_date_binning)
    elif backend == "serper":
        return search_serper(query, num_results=num_results)
    elif backend == "duckduckgo":
        return search_duckduckgo(query, num_results=num_results)
    elif backend == "googlesearch":
        return search_google_free(query, num_results=num_results, delay=delay)
    else:
        logger.error(f"Unknown search backend: {backend}")
        return []


def fetch_urls_for_board(
    board_type: str,
    num_results: int = 100,
    delay: float = 2.0,
    backend: str = "auto",
    use_date_binning: bool = True,
) -> List[Dict[str, Any]]:
    """
    Fetch job URLs for a specific job board type.

    Args:
        board_type: One of 'greenhouse', 'lever', 'ashby'
        num_results: Maximum results per query
        delay: Delay between requests
        backend: Search backend to use
        use_date_binning: Use date ranges to get more results (google_cse only)

    Returns:
        List of job payload dicts ready for upsert
    """
    if board_type not in SEARCH_QUERIES:
        logger.error(f"Unknown board type: {board_type}")
        return []

    query = SEARCH_QUERIES[board_type]
    urls = search_google(
        query,
        num_results=num_results,
        delay=delay,
        backend=backend,
        use_date_binning=use_date_binning,
    )

    payloads = []
    for url in urls:
        if not _is_valid_job_url(url, board_type):
            logger.debug(f"Skipping invalid URL: {url}")
            continue

        job_id = _extract_job_id_from_url(url, board_type)
        company_name = _extract_company_from_url(url, board_type)

        payload = {
            "source": SOURCE_MAPPING[board_type],
            "external_id": job_id,
            "title": f"Job at {company_name}",  # Placeholder - will be updated by crawler
            "organization_name": company_name,
            "organization_url": "",
            "description": "",  # Placeholder - will be updated by crawler
            "requirements": "",
            "location": "Remote",
            "job_type": "full-time",
            "application_url": url,
            "raw_data": {
                "source_url": url,
                "board_type": board_type,
                "needs_crawling": True,  # Flag for the crawler
            },
        }
        payloads.append(payload)
        logger.info(f"Found job: {url}")

    return payloads


async def import_google_search(
    boards: Optional[List[str]] = None,
    limit: int | None = None,
    dry_run: bool = False,
    use_ai: bool = False,
    batch_size: int = 20,
    num_results: int = 100,
    delay: float = 2.0,
    backend: str = "auto",
    use_date_binning: bool = True,
    unified: bool = False,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    skip_existing: bool = False,
) -> Dict[str, int]:
    """
    Import job URLs from Google Search for specified job boards.

    Args:
        boards: List of board types to search (default: all) - ignored if unified=True
        limit: Maximum jobs to process
        dry_run: If True, don't write to database
        use_ai: Whether to use AI for enrichment (not recommended for URL-only imports)
        batch_size: Batch size for AI processing
        num_results: Max results per Google search
        delay: Delay between Google requests
        backend: Search backend ('google_cse', 'serper', 'duckduckgo', 'auto')
        use_date_binning: Use date ranges to get more results (google_cse only)
        unified: Use unified search (single query for CSE restricted to job sites)
        progress_callback: Progress callback for AI processing

    Returns:
        Dict with fetched, created, updated counts
    """
    if unified:
        # Use efficient unified search for CSEs restricted to job board sites
        logger.info("Using unified search (single query for all job boards)")
        all_payloads = search_google_cse_unified(
            num_results=num_results,
            use_date_binning=use_date_binning,
        )
    else:
        # Search each board separately
        if boards is None:
            boards = list(SEARCH_QUERIES.keys())

        all_payloads = []

        for board_type in boards:
            logger.info(f"Fetching URLs for {board_type}...")
            payloads = fetch_urls_for_board(
                board_type,
                num_results=num_results,
                delay=delay,
                backend=backend,
                use_date_binning=use_date_binning,
            )
            all_payloads.extend(payloads)

            # Add delay between different board searches to avoid rate limiting
            if board_type != boards[-1]:
                time.sleep(delay)

    if limit:
        all_payloads = all_payloads[:limit]

    logger.info(f"Found {len(all_payloads)} job URLs total")

    if dry_run:
        return {"fetched": len(all_payloads), "created": 0, "updated": 0}

    # Note: AI enrichment is not very useful here since we only have URLs
    # The real enrichment should happen during crawling
    stats = await batch_upsert_jobs(
        all_payloads,
        use_ai=False,  # Force off - no content to parse yet
        batch_size=batch_size,
        progress_callback=progress_callback,
        skip_existing=skip_existing,
    )

    return stats
