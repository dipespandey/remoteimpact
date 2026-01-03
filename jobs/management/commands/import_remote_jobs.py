from __future__ import annotations

import asyncio

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand, CommandError

from jobs.services import importers, crawlers


class Command(BaseCommand):
    help = "Import remote jobs from configured external sources."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            choices=["all", "80000hours", "idealist", "reliefweb", "climatebase", "probablygood", "jobboards"],
            default="all",
            help="Limit imports to a single upstream source. 'jobboards' = Greenhouse/Lever/Ashby via Google.",
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
            help="Use Mistral AI to parse and improve job descriptions (costs money).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5,
            help="Number of jobs per AI batch when using --use-ai (default: 5, max concurrent: 3).",
        )
        parser.add_argument(
            "--provider",
            choices=["deepseek", "groq", "mistral"],
            default=None,
            help="LLM provider to use for AI parsing (default: auto-detect based on available API keys).",
        )
        parser.add_argument(
            "--new-only",
            action="store_true",
            help="Only import new jobs, skip already-imported jobs (for incremental/daily imports).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        source = options["source"]
        use_ai = options["use_ai"]
        batch_size = options["batch_size"]
        provider = options["provider"]
        new_only = options["new_only"]

        # Run the async import
        summaries = asyncio.run(
            self._run_imports(source, limit, dry_run, use_ai, batch_size, provider, new_only)
        )

        if not summaries:
            raise CommandError("No sources processed.")

        for key, summary in summaries.items():
            status = "DRY-RUN " if dry_run else ""
            skipped = summary.get('skipped', 0)
            skipped_str = f", skipped {skipped}" if skipped else ""
            self.stdout.write(
                f"{status}{key}: fetched {summary['fetched']} jobs "
                f"(created {summary['created']}, updated {summary['updated']}{skipped_str})"
            )

    async def _run_imports(
        self,
        source: str,
        limit: int | None,
        dry_run: bool,
        use_ai: bool,
        batch_size: int,
        provider: str | None,
        new_only: bool = False,
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

        if source in ("all", "80000hours"):
            self.stdout.write("Starting import from 80,000 Hours...")
            summaries["80000hours"] = await importers.import_80000_hours(
                limit=limit,
                dry_run=dry_run,
                use_ai=use_ai,
                batch_size=batch_size,
                progress_callback=make_progress_callback("80000hours") if use_ai else None,
                provider=provider,
                skip_existing=new_only,
            )

        if source in ("all", "idealist"):
            self.stdout.write("Starting import from Idealist...")
            summaries["idealist"] = await importers.import_idealist(
                limit=limit,
                dry_run=dry_run,
                use_ai=use_ai,
                batch_size=batch_size,
                progress_callback=make_progress_callback("idealist") if use_ai else None,
                provider=provider,
                skip_existing=new_only,
            )

        if source in ("all", "reliefweb"):
            self.stdout.write("Starting import from ReliefWeb...")
            summaries["reliefweb"] = await importers.import_reliefweb(
                limit=limit,
                dry_run=dry_run,
                use_ai=use_ai,
                batch_size=batch_size,
                progress_callback=make_progress_callback("reliefweb") if use_ai else None,
                provider=provider,
                skip_existing=new_only,
            )

        if source in ("all", "climatebase"):
            self.stdout.write("Starting import from Climatebase...")
            summaries["climatebase"] = await importers.import_climatebase(
                limit=limit,
                dry_run=dry_run,
                use_ai=use_ai,
                batch_size=batch_size,
                progress_callback=make_progress_callback("climatebase") if use_ai else None,
                provider=provider,
                skip_existing=new_only,
            )

        if source in ("all", "probablygood"):
            self.stdout.write("Starting import from Probably Good...")
            summaries["probablygood"] = await importers.import_probablygood(
                limit=limit,
                dry_run=dry_run,
                use_ai=use_ai,
                batch_size=batch_size,
                progress_callback=make_progress_callback("probablygood") if use_ai else None,
                provider=provider,
                skip_existing=new_only,
            )

        if source in ("all", "jobboards"):
            self.stdout.write("Starting import from job boards (Greenhouse/Lever/Ashby)...")
            # First, find new job URLs via Google search
            summaries["jobboards"] = await importers.import_google_search(
                boards=["greenhouse", "lever", "ashby"],
                limit=limit,
                dry_run=dry_run,
                use_ai=False,  # URLs only, no content yet
                backend="duckduckgo",  # Free, no API key needed
                skip_existing=new_only,
            )

            # Then crawl the job details from the APIs
            if not dry_run and summaries["jobboards"]["created"] > 0:
                self.stdout.write("Crawling job details from APIs...")
                crawl_async = sync_to_async(crawlers.crawl_jobs_needing_update, thread_sensitive=True)
                crawl_stats = await crawl_async(
                    limit=limit,
                    dry_run=dry_run,
                    delay=0.5,
                )
                self.stdout.write(
                    f"  Crawled: {crawl_stats['success']} updated, "
                    f"{crawl_stats['failed']} failed"
                )

        return summaries
