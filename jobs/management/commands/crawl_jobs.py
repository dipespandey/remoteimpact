"""
Management command to crawl job details from job board APIs.

This command fetches full job details for jobs that were discovered
via Google Search and only have placeholder data.

Usage:
    python manage.py crawl_jobs                 # Crawl all jobs needing updates
    python manage.py crawl_jobs --source lever  # Crawl only Lever jobs
    python manage.py crawl_jobs --limit 50      # Limit to 50 jobs
    python manage.py crawl_jobs --dry-run       # Preview without saving
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from jobs.services import crawlers


class Command(BaseCommand):
    help = "Crawl job details from job board APIs (Greenhouse, Lever, Ashby)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            choices=["greenhouse", "lever", "ashby"],
            default=None,
            help="Crawl only jobs from this source.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of jobs to crawl.",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.5,
            help="Delay between API requests in seconds (default: 0.5).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Crawl jobs without saving changes.",
        )

    def handle(self, *args, **options):
        source = options["source"]
        limit = options["limit"]
        delay = options["delay"]
        dry_run = options["dry_run"]

        self.stdout.write(f"Crawling jobs needing updates...")
        if source:
            self.stdout.write(f"Source filter: {source}")
        if limit:
            self.stdout.write(f"Limit: {limit}")
        self.stdout.write(f"Delay: {delay}s")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be saved"))
        self.stdout.write("")

        def progress_callback(completed: int, total: int):
            self.stdout.write(
                f"  Progress: {completed}/{total} jobs",
                ending="\r",
            )
            self.stdout.flush()
            if completed == total:
                self.stdout.write("")

        stats = crawlers.crawl_jobs_needing_update(
            source=source,
            limit=limit,
            dry_run=dry_run,
            delay=delay,
            progress_callback=progress_callback,
        )

        # Display results
        self.stdout.write("")
        status = "DRY-RUN " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{status}Crawl complete: "
                f"{stats['success']} updated, "
                f"{stats['failed']} failed, "
                f"{stats['skipped']} skipped "
                f"(of {stats['total']} total)"
            )
        )

        if stats["failed"] > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"Note: {stats['failed']} jobs failed - they may have been removed."
                )
            )
