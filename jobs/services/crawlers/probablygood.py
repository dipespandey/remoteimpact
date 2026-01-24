"""
Probably Good job crawler.

For jobs that link to external platforms (Greenhouse, Lever, Ashby),
uses the existing crawlers to get full details including posting dates.

For jobs that stay on probablygood.org, marks as crawled since
the listing page already has the best date info available.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

from jobs.models import Job

from .ashby import extract_ashby_info, fetch_ashby_job, parse_ashby_job
from .greenhouse import extract_greenhouse_info, fetch_greenhouse_job, parse_greenhouse_job
from .lever import extract_lever_info, fetch_lever_job, parse_lever_job
from .base import update_job_from_crawl

logger = logging.getLogger(__name__)


def crawl_probablygood_job(job: Job) -> Optional[Job]:
    """
    Crawl and update a Probably Good job.

    Detects the application URL domain and uses the appropriate
    platform crawler to get full details including posting date.

    Args:
        job: Job instance from probablygood source

    Returns:
        Updated Job instance or None if failed
    """
    url = job.application_url or ""
    domain = urlparse(url).netloc.lower()

    # Try Greenhouse
    if "greenhouse" in domain:
        return _crawl_via_greenhouse(job)

    # Try Lever
    if "lever" in domain:
        return _crawl_via_lever(job)

    # Try Ashby
    if "ashby" in domain:
        return _crawl_via_ashby(job)

    # For other URLs (probablygood.org, workday, etc.),
    # just mark as crawled since we can't get more date info
    logger.debug(f"No external crawler for domain: {domain}")
    raw_data = job.raw_data or {}
    raw_data["needs_crawling"] = False
    raw_data["crawl_note"] = f"No crawler available for {domain}"
    job.raw_data = raw_data
    return job


def _crawl_via_greenhouse(job: Job) -> Optional[Job]:
    """Crawl via Greenhouse API."""
    company, job_id = extract_greenhouse_info(job.application_url)

    if not company or not job_id:
        logger.warning(f"Could not extract Greenhouse info from: {job.application_url}")
        return _mark_uncrawlable(job, "Invalid Greenhouse URL")

    data = fetch_greenhouse_job(company, job_id)

    if not data:
        # Job might have been removed
        logger.info(f"Greenhouse job not found, marking inactive: {job.application_url}")
        job.is_active = False
        return _mark_uncrawlable(job, "Job not found on Greenhouse")

    parsed = parse_greenhouse_job(data)

    return update_job_from_crawl(
        job=job,
        title=parsed.get("title") or job.title,
        description=parsed.get("description") or job.description,
        location=parsed.get("location"),
        job_type=parsed.get("job_type"),
        salary_min=parsed.get("salary_min"),
        salary_max=parsed.get("salary_max"),
        salary_currency=parsed.get("salary_currency"),
        raw_api_data=data,
        posted_at=parsed.get("published_at"),
    )


def _crawl_via_lever(job: Job) -> Optional[Job]:
    """Crawl via Lever API."""
    company, job_id = extract_lever_info(job.application_url)

    if not company or not job_id:
        logger.warning(f"Could not extract Lever info from: {job.application_url}")
        return _mark_uncrawlable(job, "Invalid Lever URL")

    data = fetch_lever_job(company, job_id)

    if not data:
        logger.info(f"Lever job not found, marking inactive: {job.application_url}")
        job.is_active = False
        return _mark_uncrawlable(job, "Job not found on Lever")

    parsed = parse_lever_job(data)

    return update_job_from_crawl(
        job=job,
        title=parsed.get("title") or job.title,
        description=parsed.get("description") or job.description,
        location=parsed.get("location"),
        job_type=parsed.get("job_type"),
        salary_min=parsed.get("salary_min"),
        salary_max=parsed.get("salary_max"),
        salary_currency=parsed.get("salary_currency"),
        raw_api_data=data,
        posted_at=parsed.get("created_at"),
    )


def _crawl_via_ashby(job: Job) -> Optional[Job]:
    """Crawl via Ashby API."""
    company, job_id = extract_ashby_info(job.application_url)

    if not company or not job_id:
        logger.warning(f"Could not extract Ashby info from: {job.application_url}")
        return _mark_uncrawlable(job, "Invalid Ashby URL")

    data = fetch_ashby_job(company, job_id)

    if not data:
        logger.info(f"Ashby job not found, marking inactive: {job.application_url}")
        job.is_active = False
        return _mark_uncrawlable(job, "Job not found on Ashby")

    parsed = parse_ashby_job(data)

    return update_job_from_crawl(
        job=job,
        title=parsed.get("title") or job.title,
        description=parsed.get("description") or job.description,
        location=parsed.get("location"),
        job_type=parsed.get("job_type"),
        salary_min=parsed.get("salary_min"),
        salary_max=parsed.get("salary_max"),
        salary_currency=parsed.get("salary_currency"),
        raw_api_data=data,
        posted_at=parsed.get("published_at"),
    )


def _mark_uncrawlable(job: Job, reason: str) -> Job:
    """Mark a job as crawled but note it couldn't get external data."""
    raw_data = job.raw_data or {}
    raw_data["needs_crawling"] = False
    raw_data["crawl_error"] = reason
    job.raw_data = raw_data
    return job
