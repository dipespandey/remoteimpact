"""
CharityJob crawler.

Scrapes job details from individual CharityJob pages.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from jobs.models import Job

from .base import html_to_markdown, update_job_from_crawl

logger = logging.getLogger(__name__)

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def is_charityjob_url(url: str) -> bool:
    """Check if URL is a CharityJob job page."""
    parsed = urlparse(url)
    return "charityjob.co.uk" in parsed.netloc and "/jobs/" in parsed.path


def fetch_charityjob_page(url: str) -> Optional[str]:
    """
    Fetch the HTML content of a CharityJob job page.

    Args:
        url: Full CharityJob job URL

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
        logger.error(f"Failed to fetch CharityJob page {url}: {e}")
        return None


def parse_charityjob_page(html: str, url: str) -> dict:
    """
    Parse a CharityJob job page.

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

    # Extract organization name
    org_name = ""
    # Try to find organization link or name
    org_link = soup.find("a", href=re.compile(r"/recruiter/"))
    if org_link:
        org_name = org_link.get_text(strip=True)
    else:
        # Look for organization in meta or other elements
        org_meta = soup.find("meta", {"property": "og:site_name"})
        if org_meta:
            org_name = org_meta.get("content", "")

    # Extract main job description
    description = ""
    requirements = ""
    benefits = ""

    # CharityJob typically has job content in specific sections
    # Look for the main content area
    main_content = soup.find("div", class_=re.compile(r"job-description|job-content|description", re.I))
    if not main_content:
        # Try to find article or main content area
        main_content = soup.find("article") or soup.find("main")

    if main_content:
        # Try to find section headings and split content
        sections = {}
        current_section = "description"
        current_content = []

        for element in main_content.find_all(["h2", "h3", "h4", "p", "ul", "ol", "div"]):
            if element.name in ["h2", "h3", "h4"]:
                heading_text = element.get_text(strip=True).lower()

                # Save previous section
                if current_content:
                    sections[current_section] = "\n".join(current_content)
                    current_content = []

                # Determine new section
                if any(kw in heading_text for kw in ["about you", "requirement", "qualif", "experience", "skills", "person spec", "essential", "desirable"]):
                    current_section = "requirements"
                elif any(kw in heading_text for kw in ["benefit", "offer", "package", "perks", "what we offer"]):
                    current_section = "benefits"
                elif any(kw in heading_text for kw in ["about the role", "role", "responsibilities", "duties", "overview", "about this"]):
                    current_section = "description"
                else:
                    # Keep current section but include heading
                    current_content.append(f"**{element.get_text(strip=True)}**")
            else:
                text = element.get_text(strip=True)
                if text and len(text) > 10:
                    # Convert lists nicely
                    if element.name in ["ul", "ol"]:
                        items = element.find_all("li")
                        for item in items:
                            current_content.append(f"• {item.get_text(strip=True)}")
                    else:
                        current_content.append(text)

        # Save last section
        if current_content:
            sections[current_section] = "\n".join(current_content)

        description = sections.get("description", "")
        requirements = sections.get("requirements", "")
        benefits = sections.get("benefits", "")

        # If no structured sections found, use all content as description
        if not description and not requirements:
            all_text = main_content.get_text("\n", strip=True)
            description = all_text

    # If still no description, try getting from meta
    if not description:
        meta_desc = soup.find("meta", {"name": "description"})
        if meta_desc:
            description = meta_desc.get("content", "")

    # Extract location
    location = "Remote"
    location_el = soup.find(string=re.compile(r"Location", re.I))
    if location_el:
        parent = location_el.find_parent()
        if parent:
            next_el = parent.find_next_sibling()
            if next_el:
                location = next_el.get_text(strip=True) or "Remote"

    # Extract salary
    salary_min = None
    salary_max = None
    salary_currency = "GBP"

    salary_text = ""
    salary_el = soup.find(string=re.compile(r"Salary|Pay|Compensation", re.I))
    if salary_el:
        parent = salary_el.find_parent()
        if parent:
            salary_text = parent.get_text(" ", strip=True)

    if salary_text:
        # Parse salary like "£32,000 - £40,000" or "£35,000 per year"
        amounts = re.findall(r"[\d,]+", salary_text)
        amounts = [float(a.replace(",", "")) for a in amounts if float(a.replace(",", "")) >= 10000]

        if "$" in salary_text:
            salary_currency = "USD"
        elif "€" in salary_text:
            salary_currency = "EUR"

        if len(amounts) >= 2:
            salary_min = min(amounts)
            salary_max = max(amounts)
        elif len(amounts) == 1:
            salary_min = amounts[0]
            salary_max = amounts[0]

    # Extract job type
    job_type = "full-time"
    page_text = soup.get_text(" ", strip=True).lower()
    if "part-time" in page_text or "part time" in page_text:
        job_type = "part-time"
    elif "contract" in page_text or "temporary" in page_text or "fixed term" in page_text:
        job_type = "contract"

    return {
        "title": title,
        "description": description,
        "requirements": requirements,
        "benefits": benefits,
        "organization_name": org_name,
        "location": location,
        "job_type": job_type,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
    }


def crawl_charityjob_job(job: Job) -> Optional[Job]:
    """
    Crawl and update a CharityJob job.

    Args:
        job: Job instance with CharityJob URL

    Returns:
        Updated Job instance or None if failed
    """
    if not is_charityjob_url(job.application_url):
        logger.error(f"Not a CharityJob URL: {job.application_url}")
        return None

    # Fetch the page
    html = fetch_charityjob_page(job.application_url)

    if not html:
        # Job might have been removed - mark as inactive
        job.is_active = False
        job.raw_data = job.raw_data or {}
        job.raw_data["needs_crawling"] = False
        job.raw_data["crawl_error"] = "Job not found (404)"
        return job

    # Parse the page
    parsed = parse_charityjob_page(html, job.application_url)

    # If we got no meaningful content, mark as failed but keep active
    if not parsed["description"] or len(parsed["description"]) < 50:
        logger.warning(f"Could not extract content from {job.application_url}")
        job.raw_data = job.raw_data or {}
        job.raw_data["needs_crawling"] = False
        job.raw_data["crawl_error"] = "Could not extract content"
        return job

    # Update the job
    return update_job_from_crawl(
        job=job,
        title=parsed["title"] or job.title,
        description=parsed["description"],
        requirements=parsed["requirements"],
        location=parsed["location"],
        job_type=parsed["job_type"],
        salary_min=parsed["salary_min"],
        salary_max=parsed["salary_max"],
        salary_currency=parsed["salary_currency"],
        benefits=parsed["benefits"],
        raw_api_data={"parsed_from_html": True},
    )
