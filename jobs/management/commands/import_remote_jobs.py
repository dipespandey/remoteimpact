from __future__ import annotations

import asyncio

from asgiref.sync import sync_to_async, ThreadSensitiveContext
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from django.utils import timezone

from jobs.services import importers, crawlers
from jobs.services.importers.common import FEED_SOURCES, retire_unseen_listings


class Command(BaseCommand):
    help = "Import remote jobs from configured external sources."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            choices=["all", "feeds", "80000hours", "idealist", "reliefweb", "climatebase", "probablygood", "jobboards"],
            default="all",
            help="Limit imports to one source. 'feeds' excludes Google-discovered job boards.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after processing N jobs (useful for smoke testing).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and parse feeds without writing to the database.",
        )
        parser.add_argument(
            "--use-ai",
            action="store_true",
            help="Use DeepSeek to parse and improve job descriptions (costs money).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5,
            help="Number of jobs per AI batch when using --use-ai (default: 5, max concurrent: 3).",
        )
        parser.add_argument(
            "--provider",
            choices=["deepseek"],
            default=None,
            help="LLM provider to use for AI parsing (DeepSeek is the production default).",
        )
        parser.add_argument(
            "--new-only",
            action="store_true",
            help="Only import new jobs, skip already-imported jobs (for incremental/daily imports).",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=None,
            help="Maximum pages to scrape for paginated sources like Probably Good (default: source-specific).",
        )
        parser.add_argument(
            "--skip-maintenance",
            action="store_true",
            help="Do not run the deterministic public-inventory cleanup after a successful import.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        source = options["source"]
        use_ai = options["use_ai"]
        batch_size = options["batch_size"]
        provider = options["provider"]
        new_only = options["new_only"]
        max_pages = options["max_pages"]
        skip_maintenance = options["skip_maintenance"]

        run_started = timezone.now()
        # Run the async import
        summaries = asyncio.run(
            self._run_imports_with_cleanup(source, limit, dry_run, use_ai, batch_size, provider, new_only, max_pages)
        )

        if not summaries:
            raise CommandError("No sources processed.")

        for key, summary in summaries.items():
            status = "DRY-RUN " if dry_run else ""
            skipped = summary.get('skipped', 0)
            skipped_str = f", skipped {skipped}" if skipped else ""
            self.stdout.write(
                f"{status}{key}: fetched {summary['fetched']} jobs "
                f"(created {summary['created']}, updated {summary['updated']}"
                f"{skipped_str}, failed {summary.get('failed', 0)})"
            )

        # A complete, successful feed fetch is the aggregator's current list: anything it
        # no longer shows has been taken down upstream (usually closed by the employer).
        if not dry_run and limit is None and max_pages is None:
            for key, summary in summaries.items():
                if summary.get("error") or summary.get("failed", 0) or key not in FEED_SOURCES:
                    continue
                if summary.get("skip_retirement"):
                    self.stdout.write(f"{key}: retirement skipped because source retrieval was incomplete")
                    continue
                result = retire_unseen_listings(key, run_started, summary.get("source_fetched", summary.get("fetched", 0)))
                note = f" (skipped: {result['skipped']})" if result["skipped"] else ""
                self.stdout.write(f"{key}: retired {result['retired']} listings no longer in the feed{note}")

        failures = [
            f"{key}: {summary.get('error') or summary.get('failed', 0)}"
            for key, summary in summaries.items()
            if summary.get("error") or summary.get("failed", 0)
        ]

        # Feed imports run throughout the day. Keep jobs discovered by a later
        # import from remaining public as duplicates, expired roles, or generic
        # talent pools until the next morning's full quality pass.
        successful_import = any(not summary.get("error") for summary in summaries.values())
        if not dry_run and successful_import and not skip_maintenance:
            self.stdout.write("Running post-import public inventory maintenance...")
            for command_name in (
                "deactivate_expired_jobs",
                "deactivate_duplicate_jobs",
                "deactivate_non_opening_jobs",
            ):
                call_command(command_name, stdout=self.stdout)

        if failures and not dry_run:
            raise CommandError("Import completed with failures: " + "; ".join(failures))

    async def _run_imports_with_cleanup(self, *args):
        # Async ORM work uses another thread and therefore another connection.
        # Close it before the executor exits, including when an importer raises.
        async with ThreadSensitiveContext():
            try:
                return await self._run_imports(*args)
            finally:
                await sync_to_async(connections.close_all, thread_sensitive=True)()

    async def _run_imports(
        self,
        source: str,
        limit: int | None,
        dry_run: bool,
        use_ai: bool,
        batch_size: int,
        provider: str | None,
        new_only: bool = False,
        max_pages: int | None = None,
    ) -> dict:
        """Run imports asynchronously."""
        summaries = {}

        def make_progress_callback(source_name: str):
            def callback(completed: int, total: int):
                self.stdout.write(
                    f"  [{source_name}] AI processing: {completed}/{total} jobs",
                    ending="\r",
                )
                self.stdout.flush()
                if completed == total:
                    self.stdout.write("")  # New line after completion

            return callback

        if source in ("all", "feeds", "80000hours"):
            self.stdout.write("Starting import from 80,000 Hours...")
            try:
                summaries["80000hours"] = await importers.import_80000_hours(
                    limit=limit,
                    dry_run=dry_run,
                    use_ai=use_ai,
                    batch_size=batch_size,
                    progress_callback=make_progress_callback("80000hours") if use_ai else None,
                    provider=provider,
                    skip_existing=new_only,
                )
            except Exception as e:
                self.stderr.write(f"80000hours import failed: {e}")
                summaries["80000hours"] = {"fetched": 0, "created": 0, "updated": 0, "error": str(e)}

        if source in ("all", "feeds", "idealist"):
            self.stdout.write("Starting import from Idealist...")
            try:
                summaries["idealist"] = await importers.import_idealist(
                    limit=limit,
                    dry_run=dry_run,
                    use_ai=use_ai,
                    batch_size=batch_size,
                    progress_callback=make_progress_callback("idealist") if use_ai else None,
                    provider=provider,
                    skip_existing=new_only,
                )
            except Exception as e:
                self.stderr.write(f"idealist import failed: {e}")
                summaries["idealist"] = {"fetched": 0, "created": 0, "updated": 0, "error": str(e)}

        if source in ("all", "feeds", "reliefweb"):
            self.stdout.write("Starting import from ReliefWeb...")
            try:
                summaries["reliefweb"] = await importers.import_reliefweb(
                    limit=limit,
                    dry_run=dry_run,
                    use_ai=use_ai,
                    batch_size=batch_size,
                    progress_callback=make_progress_callback("reliefweb") if use_ai else None,
                    provider=provider,
                    skip_existing=new_only,
                )
            except Exception as e:
                self.stderr.write(f"reliefweb import failed: {e}")
                summaries["reliefweb"] = {"fetched": 0, "created": 0, "updated": 0, "error": str(e)}

        if source in ("all", "feeds", "climatebase"):
            self.stdout.write("Starting import from Climatebase...")
            try:
                summaries["climatebase"] = await importers.import_climatebase(
                    limit=limit,
                    dry_run=dry_run,
                    use_ai=use_ai,
                    batch_size=batch_size,
                    progress_callback=make_progress_callback("climatebase") if use_ai else None,
                    provider=provider,
                    skip_existing=new_only,
                )
            except Exception as e:
                self.stderr.write(f"climatebase import failed: {e}")
                summaries["climatebase"] = {"fetched": 0, "created": 0, "updated": 0, "error": str(e)}

        if source in ("all", "feeds", "probablygood"):
            self.stdout.write("Starting import from Probably Good...")
            try:
                summaries["probablygood"] = await importers.import_probablygood(
                    limit=limit,
                    max_pages=max_pages,
                    dry_run=dry_run,
                    use_ai=use_ai,
                    batch_size=batch_size,
                    progress_callback=make_progress_callback("probablygood") if use_ai else None,
                    provider=provider,
                    skip_existing=new_only,
                )
            except Exception as e:
                self.stderr.write(f"probablygood import failed: {e}")
                summaries["probablygood"] = {"fetched": 0, "created": 0, "updated": 0, "error": str(e)}

        if source in ("all", "jobboards"):
            self.stdout.write("Starting import from job boards (Greenhouse/Lever/Ashby)...")
            try:
                # First, find new job URLs via Google Custom Search
                summaries["jobboards"] = await importers.import_google_search(
                    boards=["greenhouse", "lever", "ashby"],
                    limit=limit,
                    dry_run=dry_run,
                    use_ai=False,  # URLs only, no content yet
                    backend="google_cse",  # Use Google Programmable Search
                    skip_existing=new_only,
                )

                # Then crawl job details from the APIs. Do this on every run, not
                # only when new URLs are created, so previously missed placeholders
                # get repaired instead of staying public with empty descriptions.
                if not dry_run:
                    self.stdout.write(f"Crawling job details from APIs (batch_size={batch_size})...")
                    crawl_stats = await crawlers.crawl_jobs_async(
                        limit=limit,
                        dry_run=dry_run,
                        batch_size=batch_size,
                        use_ai=use_ai,
                        provider=provider,
                        progress_callback=make_progress_callback("jobboards-crawl") if use_ai else None,
                    )
                    ai_str = " with AI" if use_ai else ""
                    self.stdout.write(
                        f"  Crawled{ai_str}: {crawl_stats['success']} updated, "
                        f"{crawl_stats.get('closed', 0)} closed, "
                        f"{crawl_stats['failed']} failed, {crawl_stats['skipped']} skipped"
                    )
                    summaries["jobboards"]["crawl_failed"] = crawl_stats["failed"]
            except Exception as e:
                self.stderr.write(f"jobboards import failed: {e}")
                summaries["jobboards"] = {"fetched": 0, "created": 0, "updated": 0, "error": str(e)}

        return summaries
