"""
Backfill Probably Good jobs with missing descriptions.

This command:
1. Finds Probably Good jobs with empty descriptions
2. Scrapes the listing page to find the correct external application URL
3. Fetches the description from the external URL
4. Updates the job with the description and correct application URL
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from jobs.models import Job
from jobs.services.importers.probablygood import fetch_job_description, FETCH_HEADERS

logger = logging.getLogger(__name__)

BASE_URL = "https://jobs.probablygood.org"
PAGE_PARAM = "b74fbe7d_page"


def get_external_url_for_slug(slug: str, max_pages: int = 60) -> Optional[str]:
    """
    Search the Probably Good listing pages to find the external URL for a job slug.
    
    Returns the external application URL if found, None otherwise.
    """
    page = 1
    
    while page <= max_pages:
        if page == 1:
            url = f"{BASE_URL}/?remote=remote"
        else:
            url = f"{BASE_URL}/?remote=remote&{PAGE_PARAM}={page}"
        
        try:
            response = requests.get(url, headers=FETCH_HEADERS, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch page {page}: {e}")
            break
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Find the job card for this slug
        job_link = soup.find("a", href=lambda x: x and f"/job-postings/{slug}" in x)
        
        if job_link:
            # Found the job - find its parent card
            card = job_link
            for _ in range(5):
                parent = card.parent
                if parent and parent.name in ["div", "article", "li", "section"]:
                    card = parent
                else:
                    break
            
            # Look for the "Job Details" button with external URL
            job_details_button = card.find("a", class_=lambda c: c and "job-details-button" in c)
            if job_details_button:
                href = job_details_button.get("href", "")
                if href and href.startswith("http") and "probablygood.org" not in href:
                    return href
            
            # Fall back to any external link
            external_links = card.find_all(
                "a", 
                href=lambda x: x and x.startswith("http") and "probablygood.org" not in x
            )
            for link in external_links:
                href = link.get("href", "")
                if href:
                    return href
            
            # Job found but no external URL
            return None
        
        # Check if there's a next page
        next_link = soup.find("a", href=lambda x: x and f"{PAGE_PARAM}={page + 1}" in x)
        if not next_link:
            break
        
        page += 1
        time.sleep(0.5)  # Rate limiting
    
    return None


class Command(BaseCommand):
    help = "Backfill Probably Good jobs with missing descriptions by re-fetching from external URLs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of jobs to process",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=1.0,
            help="Delay between requests in seconds (default: 1.0)",
        )
        parser.add_argument(
            "--url-only",
            action="store_true",
            help="Only update URLs, don't fetch descriptions",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        delay = options["delay"]
        url_only = options["url_only"]

        # Find Probably Good jobs with empty descriptions
        jobs = Job.objects.filter(
            source=Job.Source.PROBABLYGOOD,
            is_active=True,
        ).filter(
            Q(description="") | Q(description__isnull=True)
        ).order_by("-posted_at")

        if limit:
            jobs = jobs[:limit]

        total = jobs.count()
        self.stdout.write(f"Found {total} Probably Good jobs with empty descriptions")

        if dry_run:
            self.stdout.write("DRY RUN - no changes will be made")

        updated_urls = 0
        updated_descriptions = 0
        failed = 0

        for i, job in enumerate(jobs, 1):
            self.stdout.write(f"\n[{i}/{total}] Processing: {job.title}")
            
            # Get the slug from raw_data or application_url
            pg_url = job.raw_data.get("probablygood_url", "") if job.raw_data else ""
            if pg_url:
                slug = pg_url.split("/job-postings/")[-1].strip("/")
            else:
                slug = job.application_url.split("/job-postings/")[-1].strip("/")
            
            if not slug:
                self.stdout.write(self.style.WARNING(f"  No slug found, skipping"))
                failed += 1
                continue

            self.stdout.write(f"  Slug: {slug}")

            # Check if we need to find external URL
            current_url = job.application_url or ""
            needs_url = "probablygood.org" in current_url or not current_url.startswith("http")

            external_url = None
            if needs_url:
                self.stdout.write(f"  Searching for external URL...")
                external_url = get_external_url_for_slug(slug)
                
                if external_url:
                    self.stdout.write(self.style.SUCCESS(f"  Found: {external_url}"))
                    if not dry_run:
                        job.application_url = external_url
                        updated_urls += 1
                else:
                    self.stdout.write(self.style.WARNING(f"  No external URL found"))
                    external_url = current_url  # Use current URL for description fetch
            else:
                external_url = current_url
                self.stdout.write(f"  Using existing URL: {external_url}")

            # Fetch description from external URL
            got_description = False
            if not url_only and external_url and "probablygood.org" not in external_url:
                self.stdout.write(f"  Fetching description...")
                description = fetch_job_description(external_url)
                
                if description:
                    self.stdout.write(self.style.SUCCESS(f"  Got {len(description)} chars"))
                    if not dry_run:
                        job.description = description
                        got_description = True
                        updated_descriptions += 1
                else:
                    self.stdout.write(self.style.WARNING(f"  No description extracted"))
            
            # Save the job and regenerate search vector
            if not dry_run:
                # Reset search_vector so it gets regenerated with new description
                if got_description:
                    job.search_vector = None
                    job.embedding = None  # Also regenerate embedding
                job.save()
            
            time.sleep(delay)

        self.stdout.write("\n" + "=" * 50)
        status = "DRY RUN " if dry_run else ""
        self.stdout.write(
            f"{status}Results: "
            f"URLs updated: {updated_urls}, "
            f"Descriptions updated: {updated_descriptions}, "
            f"Failed: {failed}"
        )
