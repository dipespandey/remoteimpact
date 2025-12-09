"""
Base crawler utilities for fetching job details from job board APIs.
"""
from __future__ import annotations

import logging
import re
import time
from html import unescape
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from jobs.models import Job

logger = logging.getLogger(__name__)


def clean_html(html: str) -> str:
    """Remove HTML tags and clean up text."""
    if not html:
        return ""
    # Unescape HTML entities
    text = unescape(html)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Clean up whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def html_to_markdown(html: str) -> str:
    """Convert HTML to simple markdown (preserves basic formatting)."""
    if not html:
        return ""

    text = unescape(html)

    # Convert common HTML to markdown
    text = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n## \1\n", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"\n• \1", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)

    # Remove remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Clean up whitespace
    text = re.sub(r"\n\s*\n\s*\n", "\n\n", text)
    text = text.strip()

    return text


def extract_company_from_url(url: str) -> str:
    """Extract company slug from job board URL."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")

    if parts:
        # First part is usually the company slug
        company = parts[0]
        # Clean up URL encoding
        company = company.replace("%20", " ").replace("+", " ")
        return company

    return ""


def update_job_from_crawl(
    job: Job,
    title: str,
    description: str,
    company_name: Optional[str] = None,
    location: Optional[str] = None,
    job_type: Optional[str] = None,
    salary_min: Optional[float] = None,
    salary_max: Optional[float] = None,
    salary_currency: Optional[str] = None,
    requirements: Optional[str] = None,
    benefits: Optional[str] = None,
    raw_api_data: Optional[Dict] = None,
) -> Job:
    """
    Update a job record with crawled data.

    Args:
        job: Job instance to update
        title: Job title
        description: Full job description
        company_name: Company name (updates organization if different)
        location: Job location
        job_type: Job type (full-time, part-time, etc.)
        salary_min/max: Salary range
        salary_currency: Currency code
        requirements: Job requirements
        benefits: Benefits text
        raw_api_data: Raw API response data

    Returns:
        Updated Job instance
    """
    job.title = title
    job.description = description or job.description

    if location:
        job.location = location

    if job_type:
        job.job_type = job_type

    if salary_min is not None:
        job.salary_min = salary_min
    if salary_max is not None:
        job.salary_max = salary_max
    if salary_currency:
        job.salary_currency = salary_currency

    if requirements:
        job.requirements = requirements

    if benefits:
        job.benefits = benefits

    # Update raw_data
    raw_data = job.raw_data or {}
    raw_data["needs_crawling"] = False
    raw_data["crawled_at"] = timezone.now().isoformat()
    if raw_api_data:
        raw_data["api_response"] = raw_api_data
    job.raw_data = raw_data

    job.updated_at = timezone.now()

    return job


def crawl_jobs_needing_update(
    source: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    delay: float = 0.5,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, int]:
    """
    Crawl jobs that have needs_crawling=True in their raw_data.

    Args:
        source: Filter by source (greenhouse, lever, ashby)
        limit: Maximum jobs to crawl
        dry_run: If True, don't save changes
        delay: Delay between API calls
        progress_callback: Callback(completed, total) for progress updates

    Returns:
        Dict with success, failed, skipped counts
    """
    from . import crawl_greenhouse_job, crawl_lever_job, crawl_ashby_job

    # Build query
    queryset = Job.objects.filter(
        raw_data__needs_crawling=True,
        is_active=True,
    )

    if source:
        queryset = queryset.filter(source=source)

    # Get jobs that need crawling
    jobs = list(queryset[:limit] if limit else queryset)

    stats = {"success": 0, "failed": 0, "skipped": 0, "total": len(jobs)}

    logger.info(f"Found {len(jobs)} jobs to crawl")

    crawlers = {
        "greenhouse": crawl_greenhouse_job,
        "lever": crawl_lever_job,
        "ashby": crawl_ashby_job,
    }

    for i, job in enumerate(jobs):
        if progress_callback:
            progress_callback(i, len(jobs))

        crawler = crawlers.get(job.source)
        if not crawler:
            logger.warning(f"No crawler for source: {job.source}")
            stats["skipped"] += 1
            continue

        try:
            logger.info(f"Crawling [{job.source}] {job.application_url}")
            updated_job = crawler(job)

            if updated_job and not dry_run:
                with transaction.atomic():
                    updated_job.save()
                stats["success"] += 1
                logger.info(f"  Updated: {updated_job.title}")
            elif updated_job:
                stats["success"] += 1
                logger.info(f"  [DRY-RUN] Would update: {updated_job.title}")
            else:
                stats["failed"] += 1
                logger.warning(f"  Failed to crawl")

        except Exception as e:
            logger.error(f"  Error crawling {job.application_url}: {e}")
            stats["failed"] += 1

        # Rate limiting
        if i < len(jobs) - 1:
            time.sleep(delay)

    if progress_callback:
        progress_callback(len(jobs), len(jobs))

    return stats
