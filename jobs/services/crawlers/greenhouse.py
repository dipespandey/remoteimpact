"""
Greenhouse job board crawler.

API: https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import requests

from jobs.models import Job

from .base import extract_company_from_url, html_to_markdown, update_job_from_crawl

logger = logging.getLogger(__name__)

GREENHOUSE_API_BASE = "https://boards-api.greenhouse.io/v1/boards"


def extract_greenhouse_info(url: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract company and job_id from Greenhouse URL.

    Formats:
    - https://boards.greenhouse.io/{company}/jobs/{job_id}
    - https://job-boards.greenhouse.io/{company}/jobs/{job_id}

    Returns:
        (company, job_id) tuple, or (None, None) if not valid
    """
    parsed = urlparse(url)

    if "greenhouse.io" not in parsed.netloc:
        return None, None

    path = parsed.path.strip("/")
    # Pattern: company/jobs/job_id
    match = re.match(r"([^/]+)/jobs/(\d+)", path)

    if match:
        return match.group(1), match.group(2)

    return None, None


def fetch_greenhouse_job(company: str, job_id: str) -> Optional[dict]:
    """
    Fetch job details from Greenhouse API.

    Args:
        company: Company slug
        job_id: Greenhouse job ID

    Returns:
        Job data dict or None if not found
    """
    url = f"{GREENHOUSE_API_BASE}/{company}/jobs/{job_id}"

    try:
        response = requests.get(url, timeout=30)

        if response.status_code == 404:
            logger.warning(f"Job not found: {company}/{job_id}")
            return None

        response.raise_for_status()
        return response.json()

    except requests.RequestException as e:
        logger.error(f"Failed to fetch Greenhouse job {company}/{job_id}: {e}")
        return None


def parse_greenhouse_job(data: dict) -> dict:
    """
    Parse Greenhouse API response into normalized job fields.

    Args:
        data: Raw API response

    Returns:
        Normalized job fields dict
    """
    # Basic fields
    title = data.get("title", "")
    content = data.get("content", "")
    description = html_to_markdown(content)

    # Location
    location_data = data.get("location", {})
    location = location_data.get("name", "Remote")

    # Salary (if available in metadata)
    salary_min = None
    salary_max = None
    salary_currency = "USD"

    # Check metadata for salary info
    metadata = data.get("metadata") or []
    for meta in metadata:
        if meta.get("name", "").lower() in ["salary", "compensation"]:
            # Try to parse salary from value
            value = meta.get("value", "")
            # Simple pattern matching for salary ranges
            salary_match = re.search(r"\$?([\d,]+)\s*[-–]\s*\$?([\d,]+)", value)
            if salary_match:
                try:
                    salary_min = float(salary_match.group(1).replace(",", ""))
                    salary_max = float(salary_match.group(2).replace(",", ""))
                except ValueError:
                    pass

    # Job type detection
    job_type = "full-time"
    title_lower = title.lower()
    if "part-time" in title_lower or "part time" in title_lower:
        job_type = "part-time"
    elif "contract" in title_lower or "contractor" in title_lower:
        job_type = "contract"
    elif "freelance" in title_lower:
        job_type = "freelance"

    # Departments/teams as categories
    departments = data.get("departments", [])
    department_names = [d.get("name", "") for d in departments if d.get("name")]

    # Extract date (updated_at is when the job was last modified)
    updated_at = data.get("updated_at")

    return {
        "title": title,
        "description": description,
        "location": location,
        "job_type": job_type,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "departments": department_names,
        "absolute_url": data.get("absolute_url", ""),
        "updated_at": updated_at,
    }


def crawl_greenhouse_job(job: Job) -> Optional[Job]:
    """
    Crawl and update a Greenhouse job.

    Args:
        job: Job instance with Greenhouse URL

    Returns:
        Updated Job instance or None if failed
    """
    company, job_id = extract_greenhouse_info(job.application_url)

    if not company or not job_id:
        logger.error(f"Could not extract Greenhouse info from: {job.application_url}")
        return None

    # Fetch from API
    data = fetch_greenhouse_job(company, job_id)

    if not data:
        # Job might have been removed - mark as inactive
        job.is_active = False
        job.raw_data = job.raw_data or {}
        job.raw_data["needs_crawling"] = False
        job.raw_data["crawl_error"] = "Job not found (404)"
        return job

    # Parse the data
    parsed = parse_greenhouse_job(data)

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
        posted_at=parsed.get("updated_at"),
    )
