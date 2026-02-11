"""
Idealist job crawler.

Scrapes job details from idealist.org job pages.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from jobs.models import Job

from .base import html_to_markdown, update_job_from_crawl, extract_salary_from_text

logger = logging.getLogger(__name__)

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def is_idealist_url(url: str) -> bool:
    """Check if URL is an Idealist job page."""
    parsed = urlparse(url)
    return "idealist.org" in parsed.netloc


def fetch_idealist_page(url: str) -> Optional[str]:
    """
    Fetch the HTML content of an Idealist job page.

    Args:
        url: Full Idealist job URL

    Returns:
        HTML content or None if failed
    """
    try:
        response = requests.get(url, headers=FETCH_HEADERS, timeout=30)

        if response.status_code == 404:
            logger.warning(f"Job not found: {url}")
            return None

        response.raise_for_status()
        return response.text

    except requests.RequestException as e:
        logger.error(f"Failed to fetch Idealist page {url}: {e}")
        return None


def parse_idealist_page(html: str, url: str) -> dict:
    """
    Parse an Idealist job page.

    Args:
        html: Raw HTML content
        url: Original URL for logging

    Returns:
        Dict with parsed job fields
    """
    soup = BeautifulSoup(html, "html.parser")

    # Extract title
    title = ""
    title_el = soup.find("h1")
    if title_el:
        title = title_el.get_text(strip=True)

    # Extract description - main content area
    description = ""
    
    # Try to find the job description section
    # Idealist uses various selectors for content
    content_selectors = [
        "div[data-testid='job-description']",
        "div.job-description",
        "div.listing-body",
        "article",
        "main",
    ]
    
    for selector in content_selectors:
        content_el = soup.select_one(selector)
        if content_el:
            description = html_to_markdown(str(content_el))
            if len(description) > 200:
                break
    
    # Fallback: get all text from body
    if len(description) < 200:
        body = soup.find("body")
        if body:
            # Remove script and style tags
            for tag in body.find_all(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            description = html_to_markdown(str(body))

    # Extract location
    location = "Remote"
    location_el = soup.find(string=re.compile(r"Location", re.I))
    if location_el:
        parent = location_el.find_parent()
        if parent:
            location_text = parent.get_text(strip=True)
            # Clean up the location
            location = location_text.replace("Location:", "").replace("Location", "").strip()

    # Extract salary if present
    salary_min = None
    salary_max = None
    salary_currency = "USD"
    
    salary_info = extract_salary_from_text(description)
    if salary_info:
        salary_min = salary_info.get("salary_min")
        salary_max = salary_info.get("salary_max")
        salary_currency = salary_info.get("salary_currency", "USD")

    # Detect job type
    job_type = "full-time"
    text_lower = (title + " " + description).lower()
    if "part-time" in text_lower or "part time" in text_lower:
        job_type = "part-time"
    elif "contract" in text_lower:
        job_type = "contract"
    elif "intern" in text_lower:
        job_type = "internship"

    return {
        "title": title,
        "description": description,
        "location": location,
        "job_type": job_type,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
    }


def crawl_idealist_job(job: Job) -> Optional[Job]:
    """
    Crawl and update an Idealist job.

    Args:
        job: Job instance with Idealist URL

    Returns:
        Updated Job instance or None if failed
    """
    if not is_idealist_url(job.application_url or ""):
        logger.warning(f"Not an Idealist URL: {job.application_url}")
        return None

    # Fetch the page
    html = fetch_idealist_page(job.application_url)

    if not html:
        # Job might have been removed - mark as inactive
        job.is_active = False
        job.raw_data = job.raw_data or {}
        job.raw_data["needs_crawling"] = False
        job.raw_data["crawl_error"] = "Job not found (404 or fetch error)"
        return job

    # Parse the page
    parsed = parse_idealist_page(html, job.application_url)

    # Only update if we got meaningful content
    if len(parsed.get("description", "")) < 100:
        logger.warning(f"Could not extract description from: {job.application_url}")
        job.raw_data = job.raw_data or {}
        job.raw_data["needs_crawling"] = False
        job.raw_data["crawl_note"] = "Could not extract description"
        return job

    # Update the job
    return update_job_from_crawl(
        job=job,
        title=parsed.get("title") or job.title,
        description=parsed["description"],
        location=parsed.get("location"),
        job_type=parsed.get("job_type"),
        salary_min=parsed.get("salary_min"),
        salary_max=parsed.get("salary_max"),
        salary_currency=parsed.get("salary_currency"),
        raw_api_data={"source": "idealist_scrape"},
    )
