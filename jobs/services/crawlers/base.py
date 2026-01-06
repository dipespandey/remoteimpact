"""
Base crawler utilities for fetching job details from job board APIs.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from asgiref.sync import sync_to_async
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
    posted_at: Optional[Any] = None,
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
        posted_at: Original publish date from source (datetime or ISO string)

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

    # Update posted_at from source if provided
    if posted_at:
        if isinstance(posted_at, datetime):
            job.posted_at = posted_at
        elif isinstance(posted_at, str):
            try:
                # Parse ISO format datetime string
                parsed_date = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
                job.posted_at = parsed_date
            except (ValueError, AttributeError):
                logger.warning(f"Could not parse posted_at: {posted_at}")

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


async def crawl_jobs_async(
    source: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    batch_size: int = 20,
    use_ai: bool = False,
    provider: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, int]:
    """
    Crawl jobs in parallel with optional AI enrichment.

    Args:
        source: Filter by source (greenhouse, lever, ashby)
        limit: Maximum jobs to crawl
        dry_run: If True, don't save changes
        batch_size: Number of concurrent API requests
        use_ai: If True, run AI enrichment on descriptions
        provider: LLM provider for AI enrichment
        progress_callback: Callback(completed, total) for progress updates

    Returns:
        Dict with success, failed, skipped counts
    """
    from . import crawl_greenhouse_job, crawl_lever_job, crawl_ashby_job

    # Build query with organization pre-fetched
    queryset = Job.objects.select_related('organization').filter(
        raw_data__needs_crawling=True,
        is_active=True,
    )

    if source:
        queryset = queryset.filter(source=source)

    # Get jobs that need crawling
    get_jobs = sync_to_async(lambda: list(queryset[:limit] if limit else queryset), thread_sensitive=True)
    jobs = await get_jobs()

    stats = {"success": 0, "failed": 0, "skipped": 0, "total": len(jobs)}

    if not jobs:
        return stats

    logger.info(f"Found {len(jobs)} jobs to crawl in parallel (batch_size={batch_size})")

    crawlers = {
        "greenhouse": crawl_greenhouse_job,
        "lever": crawl_lever_job,
        "ashby": crawl_ashby_job,
    }

    def crawl_single_job(job: Job) -> tuple[Job, Optional[Job], Optional[str]]:
        """Crawl a single job synchronously. Returns (original_job, updated_job, error)."""
        crawler = crawlers.get(job.source)
        if not crawler:
            return job, None, f"No crawler for source: {job.source}"

        try:
            updated_job = crawler(job)
            return job, updated_job, None
        except Exception as e:
            return job, None, str(e)

    # Process in batches using ThreadPoolExecutor
    completed = 0
    jobs_to_save = []

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i + batch_size]

        # Run batch in parallel using threads (for I/O-bound API calls)
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            results = await loop.run_in_executor(
                None,
                lambda b=batch: list(executor.map(crawl_single_job, b))
            )

        # Process results
        for original_job, updated_job, error in results:
            if error:
                if "No crawler" in error:
                    stats["skipped"] += 1
                else:
                    stats["failed"] += 1
                    logger.error(f"Error crawling {original_job.application_url}: {error}")
            elif updated_job:
                if updated_job.is_active:
                    jobs_to_save.append(updated_job)
                    stats["success"] += 1
                else:
                    # Job was marked inactive (404)
                    jobs_to_save.append(updated_job)
                    stats["failed"] += 1
            else:
                stats["failed"] += 1

            completed += 1

        if progress_callback:
            progress_callback(completed, len(jobs))

        logger.info(f"Crawled batch {i // batch_size + 1}: {completed}/{len(jobs)} jobs")

    # AI enrichment if requested
    if use_ai and jobs_to_save:
        active_jobs = [j for j in jobs_to_save if j.is_active and j.description]
        if active_jobs:
            logger.info(f"Running AI enrichment on {len(active_jobs)} jobs...")
            from jobs.services.importers.common import batch_process_with_ai

            # Convert jobs to payloads for AI processing (org already prefetched)
            def build_payloads(jobs_list):
                return [{
                    "job_id": job.id,
                    "title": job.title,
                    "description": job.description,
                    "requirements": job.requirements or "",
                    "organization_name": job.organization.name if job.organization_id else "",
                } for job in jobs_list]

            build_payloads_async = sync_to_async(build_payloads, thread_sensitive=True)
            payloads = await build_payloads_async(active_jobs)

            # Process with AI
            enriched = await batch_process_with_ai(
                payloads,
                batch_size=batch_size,
                provider=provider,
            )

            # Update jobs with enriched data
            enriched_map = {e["job_id"]: e for e in enriched if "job_id" in e}
            for job in active_jobs:
                if job.id in enriched_map:
                    e = enriched_map[job.id]
                    if e.get("description"):
                        job.description = e["description"]
                    if e.get("requirements"):
                        job.requirements = e["requirements"]
                    if e.get("impact"):
                        job.impact = e["impact"]
                    if e.get("benefits"):
                        job.benefits = e["benefits"]
                    if e.get("skills") and isinstance(e["skills"], list):
                        job.skills = e["skills"]

    # Save all jobs
    if not dry_run and jobs_to_save:
        save_jobs = sync_to_async(_save_jobs_batch, thread_sensitive=True)
        await save_jobs(jobs_to_save)
        logger.info(f"Saved {len(jobs_to_save)} jobs")

    return stats


def _save_jobs_batch(jobs: List[Job]) -> None:
    """Save a batch of jobs in a single transaction."""
    with transaction.atomic():
        for job in jobs:
            job.save()
