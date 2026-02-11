"""
Backfill job descriptions from JS-rendered pages using Playwright.

Targets:
- Probably Good jobs with probablygood.org detail page URLs
- Workday jobs (myworkdayjobs.com)
- Other JS-heavy career sites
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from django.core.management.base import BaseCommand
from django.db.models import Q

from jobs.models import Job

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill job descriptions from JS-rendered pages using Playwright"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum number of jobs to process (default: 50)",
        )
        parser.add_argument(
            "--source",
            choices=["probablygood", "workday", "all"],
            default="all",
            help="Which job sources to process",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=2.0,
            help="Delay between requests in seconds (default: 2.0)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        source = options["source"]
        delay = options["delay"]

        # Import Playwright scraper
        try:
            from jobs.services.crawlers.playwright_scraper import fetch_page_content
        except ImportError as e:
            self.stderr.write(self.style.ERROR(f"Failed to import Playwright scraper: {e}"))
            self.stderr.write("Make sure Playwright is installed: pip install playwright && playwright install chromium")
            return

        # Build query for jobs needing descriptions
        jobs_query = Job.objects.filter(
            is_active=True,
        ).filter(
            Q(description="") | Q(description__isnull=True)
        )

        if source == "probablygood":
            jobs_query = jobs_query.filter(
                source=Job.Source.PROBABLYGOOD,
                application_url__icontains="probablygood.org"
            )
        elif source == "workday":
            jobs_query = jobs_query.filter(
                application_url__icontains="workday"
            )
        else:
            # All JS-rendered sources
            jobs_query = jobs_query.filter(
                Q(application_url__icontains="probablygood.org") |
                Q(application_url__icontains="workday") |
                Q(application_url__icontains="icims.com")
            )

        jobs = jobs_query.order_by("-posted_at")[:limit]
        total = len(jobs)

        self.stdout.write(f"Found {total} jobs to process")
        if dry_run:
            self.stdout.write("DRY RUN - no changes will be made")

        updated = 0
        failed = 0

        for i, job in enumerate(jobs, 1):
            url = job.application_url
            self.stdout.write(f"\n[{i}/{total}] {job.title}")
            self.stdout.write(f"  URL: {url}")

            if dry_run:
                self.stdout.write("  [DRY RUN] Would fetch description")
                continue

            # Fetch using Playwright
            self.stdout.write("  Fetching with Playwright...")
            description = fetch_page_content(url)

            if description and len(description) > 100:
                self.stdout.write(self.style.SUCCESS(f"  Got {len(description)} chars"))
                job.description = description
                job.search_vector = None  # Reset for regeneration
                job.embedding = None
                job.save()
                updated += 1
            else:
                self.stdout.write(self.style.WARNING("  No description extracted"))
                failed += 1

            # Rate limiting
            if i < total:
                time.sleep(delay)

        self.stdout.write("\n" + "=" * 50)
        status = "DRY RUN " if dry_run else ""
        self.stdout.write(f"{status}Results: Updated {updated}, Failed {failed}")
