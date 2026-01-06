"""
Ashby job board crawler.

API: https://api.ashbyhq.com/posting-api/job-board/{company}
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse, unquote

import requests

from jobs.models import Job

from .base import html_to_markdown, update_job_from_crawl

logger = logging.getLogger(__name__)

ASHBY_API_BASE = "https://api.ashbyhq.com/posting-api/job-board"


def extract_ashby_info(url: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract company and job_id from Ashby URL.

    Formats:
    - https://jobs.ashbyhq.com/{company}/{job_id}
    - https://jobs.ashbyhq.com/{company}/{job_id}/application

    Returns:
        (company, job_id) tuple, or (None, None) if not valid
    """
    parsed = urlparse(url)

    if "ashbyhq.com" not in parsed.netloc:
        return None, None

    path = parsed.path.strip("/")
    parts = path.split("/")

    if len(parts) >= 2:
        # Company might be URL encoded (e.g., "Solana%20Foundation")
        company = unquote(parts[0])
        job_id = parts[1]
        return company, job_id

    return None, None


def fetch_ashby_job(company: str, job_id: str) -> Optional[dict]:
    """
    Fetch job details from Ashby API.

    Note: Ashby API returns all jobs for a company, so we filter by ID.

    Args:
        company: Company slug
        job_id: Ashby job ID (UUID)

    Returns:
        Job data dict or None if not found
    """
    url = f"{ASHBY_API_BASE}/{company}"

    try:
        response = requests.get(url, timeout=30)

        if response.status_code == 404:
            logger.warning(f"Company not found: {company}")
            return None

        response.raise_for_status()
        data = response.json()
        jobs = data.get("jobs", [])

        # Find the specific job by ID
        for job in jobs:
            if job.get("id") == job_id:
                return job

        logger.warning(f"Job {job_id} not found in {company} listings")
        return None

    except requests.RequestException as e:
        logger.error(f"Failed to fetch Ashby job {company}/{job_id}: {e}")
        return None


def parse_ashby_job(data: dict) -> dict:
    """
    Parse Ashby API response into normalized job fields.

    Args:
        data: Raw API response

    Returns:
        Normalized job fields dict
    """
    # Basic fields
    title = data.get("title", "")
    description_html = data.get("descriptionHtml", "")
    description = html_to_markdown(description_html)

    # Location
    location = data.get("location", "Remote")
    is_remote = data.get("isRemote", False)

    if is_remote:
        if location and "remote" not in location.lower():
            location = f"{location} (Remote)"
        elif not location:
            location = "Remote"

    # Secondary locations (can be list of dicts with 'location' key)
    secondary = data.get("secondaryLocations", [])
    if secondary:
        secondary_names = []
        for loc in secondary:
            if isinstance(loc, dict):
                secondary_names.append(loc.get("location", ""))
            elif isinstance(loc, str):
                secondary_names.append(loc)
        if secondary_names:
            # Just note that there are multiple locations, don't list all
            location = f"{location} (+{len(secondary_names)} locations)"

    # Job type from employmentType
    employment_type = data.get("employmentType", "").lower()
    job_type = "full-time"
    if employment_type == "parttime":
        job_type = "part-time"
    elif employment_type == "contract" or employment_type == "contractor":
        job_type = "contract"
    elif employment_type == "freelance":
        job_type = "freelance"
    elif employment_type == "intern" or employment_type == "internship":
        job_type = "contract"  # Map internship to contract

    # Salary (Ashby sometimes has this in compensation field)
    salary_min = None
    salary_max = None
    salary_currency = "USD"

    compensation = data.get("compensation")
    if compensation:
        salary_min = compensation.get("min")
        salary_max = compensation.get("max")
        salary_currency = compensation.get("currency", "USD")

    # Department/team
    department = data.get("department", "")
    team = data.get("team", "")

    # Extract publish date
    published_at = data.get("publishedAt")

    return {
        "title": title,
        "description": description,
        "location": location,
        "job_type": job_type,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "department": department,
        "team": team,
        "is_remote": is_remote,
        "job_url": data.get("jobUrl", ""),
        "apply_url": data.get("applyUrl", ""),
        "published_at": published_at,
    }


def crawl_ashby_job(job: Job) -> Optional[Job]:
    """
    Crawl and update an Ashby job.

    Args:
        job: Job instance with Ashby URL

    Returns:
        Updated Job instance or None if failed
    """
    company, job_id = extract_ashby_info(job.application_url)

    if not company or not job_id:
        logger.error(f"Could not extract Ashby info from: {job.application_url}")
        return None

    # Fetch from API
    data = fetch_ashby_job(company, job_id)

    if not data:
        # Job might have been removed - mark as inactive
        job.is_active = False
        job.raw_data = job.raw_data or {}
        job.raw_data["needs_crawling"] = False
        job.raw_data["crawl_error"] = "Job not found"
        return job

    # Parse the data
    parsed = parse_ashby_job(data)

    # Update the job
    return update_job_from_crawl(
        job=job,
        title=parsed["title"],
        description=parsed["description"],
        location=parsed["location"],
        job_type=parsed["job_type"],
        salary_min=parsed["salary_min"],
        salary_max=parsed["salary_max"],
        salary_currency=parsed["salary_currency"],
        raw_api_data=data,
        posted_at=parsed.get("published_at"),
    )
