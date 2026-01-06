"""
Lever job board crawler.

API: https://api.lever.co/v0/postings/{company}?mode=json
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import requests

from jobs.models import Job

from .base import html_to_markdown, update_job_from_crawl

logger = logging.getLogger(__name__)

LEVER_API_BASE = "https://api.lever.co/v0/postings"


def extract_lever_info(url: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract company and job_id from Lever URL.

    Formats:
    - https://jobs.lever.co/{company}/{job_id}
    - https://jobs.lever.co/{company}/{job_id}/apply

    Returns:
        (company, job_id) tuple, or (None, None) if not valid
    """
    parsed = urlparse(url)

    if "lever.co" not in parsed.netloc:
        return None, None

    path = parsed.path.strip("/")
    parts = path.split("/")

    if len(parts) >= 2:
        company = parts[0]
        job_id = parts[1]
        return company, job_id

    return None, None


def fetch_lever_job(company: str, job_id: str) -> Optional[dict]:
    """
    Fetch job details from Lever API.

    Note: Lever API returns all jobs for a company, so we filter by ID.

    Args:
        company: Company slug
        job_id: Lever job ID (UUID)

    Returns:
        Job data dict or None if not found
    """
    url = f"{LEVER_API_BASE}/{company}?mode=json"

    try:
        response = requests.get(url, timeout=30)

        if response.status_code == 404:
            logger.warning(f"Company not found: {company}")
            return None

        response.raise_for_status()
        jobs = response.json()

        # Find the specific job by ID
        for job in jobs:
            if job.get("id") == job_id:
                return job

        logger.warning(f"Job {job_id} not found in {company} listings")
        return None

    except requests.RequestException as e:
        logger.error(f"Failed to fetch Lever job {company}/{job_id}: {e}")
        return None


def parse_lever_job(data: dict) -> dict:
    """
    Parse Lever API response into normalized job fields.

    Args:
        data: Raw API response

    Returns:
        Normalized job fields dict
    """
    # Basic fields
    title = data.get("text", "")
    description_html = data.get("descriptionBody") or data.get("description", "")
    description = html_to_markdown(description_html)

    # Additional info (often contains requirements/qualifications)
    additional_html = data.get("additional", "")
    requirements = html_to_markdown(additional_html) if additional_html else ""

    # Location from categories
    categories = data.get("categories", {})
    location = categories.get("location", "Remote")

    # If workplaceType is remote, ensure location reflects that
    if data.get("workplaceType") == "remote":
        if "remote" not in location.lower():
            location = f"{location} (Remote)" if location else "Remote"

    # Job type from commitment
    commitment = categories.get("commitment", "").lower()
    job_type = "full-time"
    if "part-time" in commitment or "part time" in commitment:
        job_type = "part-time"
    elif "contract" in commitment:
        job_type = "contract"
    elif "freelance" in commitment:
        job_type = "freelance"

    # Salary (Lever often has structured salary data)
    salary_range = data.get("salaryRange", {})
    salary_min = salary_range.get("min")
    salary_max = salary_range.get("max")
    salary_currency = salary_range.get("currency", "USD")

    # Benefits from salary description
    benefits_html = data.get("salaryDescription", "")
    benefits = html_to_markdown(benefits_html) if benefits_html else ""

    # Team/department
    team = categories.get("team", "")

    # Extract created date (epoch timestamp in milliseconds)
    created_at_ms = data.get("createdAt")
    created_at = None
    if created_at_ms and isinstance(created_at_ms, (int, float)):
        try:
            created_at = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc)
        except (ValueError, OSError):
            pass

    return {
        "title": title,
        "description": description,
        "requirements": requirements,
        "location": location,
        "job_type": job_type,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "benefits": benefits,
        "team": team,
        "hosted_url": data.get("hostedUrl", ""),
        "apply_url": data.get("applyUrl", ""),
        "created_at": created_at,
    }


def crawl_lever_job(job: Job) -> Optional[Job]:
    """
    Crawl and update a Lever job.

    Args:
        job: Job instance with Lever URL

    Returns:
        Updated Job instance or None if failed
    """
    company, job_id = extract_lever_info(job.application_url)

    if not company or not job_id:
        logger.error(f"Could not extract Lever info from: {job.application_url}")
        return None

    # Fetch from API
    data = fetch_lever_job(company, job_id)

    if not data:
        # Job might have been removed - mark as inactive
        job.is_active = False
        job.raw_data = job.raw_data or {}
        job.raw_data["needs_crawling"] = False
        job.raw_data["crawl_error"] = "Job not found"
        return job

    # Parse the data
    parsed = parse_lever_job(data)

    # Update the job
    return update_job_from_crawl(
        job=job,
        title=parsed["title"],
        description=parsed["description"],
        requirements=parsed["requirements"],
        location=parsed["location"],
        job_type=parsed["job_type"],
        salary_min=parsed["salary_min"],
        salary_max=parsed["salary_max"],
        salary_currency=parsed["salary_currency"],
        benefits=parsed["benefits"],
        raw_api_data=data,
        posted_at=parsed.get("created_at"),
    )
