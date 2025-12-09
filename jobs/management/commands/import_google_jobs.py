"""
Management command to import job URLs from Google Search.

This command searches Google for job listings on Greenhouse, Lever, and Ashby
job boards and saves the URLs to the database for later crawling.

Usage:
    python manage.py import_google_jobs                    # Search all boards
    python manage.py import_google_jobs --board greenhouse # Search specific board
    python manage.py import_google_jobs --limit 50         # Limit results
    python manage.py import_google_jobs --dry-run          # Preview without saving
    python manage.py import_google_jobs --num-results 200  # More results per board
"""
from __future__ import annotations

import asyncio

from django.core.management.base import BaseCommand, CommandError

from jobs.services import importers


class Command(BaseCommand):
    help = "Import job URLs from Google Search (Greenhouse, Lever, Ashby job boards)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--board",
            choices=["all", "greenhouse", "lever", "ashby"],
            default="all",
            help="Job board to search (default: all).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after processing N jobs total (useful for testing).",
        )
        parser.add_argument(
            "--num-results",
            type=int,
            default=100,
            help="Maximum results to fetch per board (default: 100).",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=2.0,
            help="Delay between Google requests in seconds (default: 2.0).",
        )
        parser.add_argument(
            "--backend",
            choices=["auto", "google_cse", "duckduckgo", "serper", "googlesearch"],
            default="auto",
            help="Search backend: 'google_cse' (100/day, recommended), 'duckduckgo' (free), 'serper', 'auto' (default).",
        )
        parser.add_argument(
            "--no-date-binning",
            action="store_true",
            help="Disable date binning for Google CSE (uses fewer queries but gets fewer results).",
        )
        parser.add_argument(
            "--unified",
            action="store_true",
            help="Use unified search (recommended if CSE is restricted to job sites). More efficient.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch URLs without writing to the database.",
        )

    def handle(self, *args, **options):
        board = options["board"]
        limit = options["limit"]
        num_results = options["num_results"]
        delay = options["delay"]
        backend = options["backend"]
        use_date_binning = not options["no_date_binning"]
        unified = options["unified"]
        dry_run = options["dry_run"]

        # Determine which boards to search
        if board == "all":
            boards = ["greenhouse", "lever", "ashby"]
        else:
            boards = [board]

        if unified:
            self.stdout.write("Unified search mode (single query for all job boards)")
        else:
            self.stdout.write(f"Searching for jobs on: {', '.join(boards)}")
        self.stdout.write(f"Max results: {num_results}")
        self.stdout.write(f"Search backend: {backend}")
        self.stdout.write(f"Date binning: {'enabled' if use_date_binning else 'disabled'}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be saved"))
        self.stdout.write("")

        # Run the async import
        summary = asyncio.run(
            importers.import_google_search(
                boards=boards,
                limit=limit,
                dry_run=dry_run,
                num_results=num_results,
                delay=delay,
                backend=backend,
                use_date_binning=use_date_binning,
                unified=unified,
            )
        )

        # Display results
        status = "DRY-RUN " if dry_run else ""
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{status}Google Search: fetched {summary['fetched']} URLs "
                f"(created {summary['created']}, updated {summary['updated']})"
            )
        )

        if summary["created"] > 0:
            self.stdout.write("")
            self.stdout.write(
                self.style.NOTICE(
                    "Note: Jobs have placeholder data. Run the crawler to fetch full details."
                )
            )
