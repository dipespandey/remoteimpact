"""
Management command to backfill skills into existing jobs using LLM extraction.

Usage:
    python manage.py backfill_skills --dry-run --limit 10
    python manage.py backfill_skills --batch-size 20 --limit 100
    python manage.py backfill_skills  # Process all jobs without skills
"""

import asyncio
import time
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

from jobs.models import Job
from jobs.services.llm_parser import JobParser


class Command(BaseCommand):
    help = "Backfill skills into jobs that have descriptions but no skills extracted"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of jobs to process (default: all)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10,
            help="Number of jobs to process per batch (default: 10)",
        )
        parser.add_argument(
            "--provider",
            type=str,
            default=None,
            help="LLM provider to use: deepseek, groq, or mistral (default: auto-detect)",
        )
        parser.add_argument(
            "--min-description-length",
            type=int,
            default=100,
            help="Minimum description length to process (default: 100)",
        )
        parser.add_argument(
            "--active-only",
            action="store_true",
            default=True,
            help="Only process active jobs (default: True)",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="Include inactive jobs",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        batch_size = options["batch_size"]
        provider = options["provider"]
        min_desc_length = options["min_description_length"]
        active_only = options["active_only"] and not options["include_inactive"]

        self.stdout.write(self.style.NOTICE("=" * 60))
        self.stdout.write(self.style.NOTICE("Skills Backfill Command"))
        self.stdout.write(self.style.NOTICE("=" * 60))

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be saved"))

        # Build the query for jobs needing skills
        queryset = Job.objects.filter(
            Q(skills=[]) | Q(skills__isnull=True)
        ).exclude(
            description=""
        ).exclude(
            description__isnull=True
        )

        if active_only:
            now = timezone.now()
            cutoff = now - timedelta(days=180)
            queryset = queryset.filter(is_active=True).exclude(
                expires_at__lt=now
            ).exclude(
                expires_at__isnull=True,
                posted_at__lt=cutoff,
            )

        # Filter by minimum description length (use table prefix to avoid ambiguity)
        queryset = queryset.extra(
            where=["LENGTH(jobs_job.description) >= %s"],
            params=[min_desc_length]
        )

        total_count = queryset.count()
        self.stdout.write(f"\nFound {total_count} jobs needing skills backfill")

        if limit:
            queryset = queryset[:limit]
            self.stdout.write(f"Processing limited to {limit} jobs")

        jobs_to_process = list(queryset.select_related("organization"))
        
        if not jobs_to_process:
            self.stdout.write(self.style.SUCCESS("No jobs to process. All done!"))
            return

        self.stdout.write(f"Batch size: {batch_size}")
        self.stdout.write(f"Provider: {provider or 'auto-detect'}")
        self.stdout.write("")

        # Run the async processing
        asyncio.run(self._process_jobs(
            jobs_to_process,
            batch_size,
            provider,
            dry_run,
        ))

    async def _process_jobs(self, jobs, batch_size, provider, dry_run):
        """Process jobs in batches using the LLM parser."""
        
        # Initialize parser
        try:
            parser = JobParser(provider=provider)
            self.stdout.write(f"Using LLM provider: {parser.provider_name}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to initialize parser: {e}"))
            return

        total = len(jobs)
        processed = 0
        updated = 0
        errors = 0
        start_time = time.time()

        # Process in batches
        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch = jobs[batch_start:batch_end]
            batch_num = (batch_start // batch_size) + 1
            total_batches = (total + batch_size - 1) // batch_size

            self.stdout.write(f"\n--- Batch {batch_num}/{total_batches} ---")

            # Prepare payloads for the batch
            payloads = []
            job_map = {}  # Map payload index to job
            
            for i, job in enumerate(batch):
                payload = {
                    "title": job.title,
                    "organization_name": job.organization.name if job.organization else "Unknown",
                    "description": job.description or "",
                }
                payloads.append(payload)
                job_map[i] = job

            # Process batch
            try:
                def progress_callback(completed, total):
                    pass  # Silent progress

                results = await parser.parse_batch(
                    payloads,
                    batch_size=len(payloads),
                    progress_callback=progress_callback,
                )

                # Update jobs with extracted skills
                for i, result in enumerate(results):
                    job = job_map[i]
                    processed += 1
                    
                    skills = result.get("skills", [])
                    
                    if skills and isinstance(skills, list) and len(skills) > 0:
                        # Clean and validate skills
                        clean_skills = [
                            s.strip().lower().replace(" ", "-") 
                            for s in skills 
                            if isinstance(s, str) and s.strip()
                        ][:15]  # Limit to 15 skills
                        
                        if clean_skills:
                            if dry_run:
                                self.stdout.write(
                                    f"  [DRY] Job {job.id}: {job.title[:50]}..."
                                )
                                self.stdout.write(
                                    f"        Would set skills: {clean_skills}"
                                )
                            else:
                                job.skills = clean_skills
                                job.save(update_fields=["skills"])
                                self.stdout.write(
                                    self.style.SUCCESS(
                                        f"  ✓ Job {job.id}: {len(clean_skills)} skills extracted"
                                    )
                                )
                            updated += 1
                        else:
                            self.stdout.write(
                                f"  - Job {job.id}: No valid skills extracted"
                            )
                    else:
                        self.stdout.write(
                            f"  - Job {job.id}: No skills returned by LLM"
                        )

            except Exception as e:
                errors += len(batch)
                self.stdout.write(
                    self.style.ERROR(f"  Batch error: {e}")
                )

            # Rate limiting between batches
            if batch_end < total:
                delay = 2.0  # 2 seconds between batches
                self.stdout.write(f"  Waiting {delay}s before next batch...")
                await asyncio.sleep(delay)

        # Summary
        elapsed = time.time() - start_time
        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("=" * 60))
        self.stdout.write(self.style.NOTICE("Summary"))
        self.stdout.write(self.style.NOTICE("=" * 60))
        self.stdout.write(f"Total processed: {processed}")
        self.stdout.write(f"Successfully updated: {updated}")
        self.stdout.write(f"Errors: {errors}")
        self.stdout.write(f"Time elapsed: {elapsed:.1f}s")
        
        if dry_run:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "This was a dry run. Run without --dry-run to apply changes."
            ))
        else:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("Backfill complete!"))
