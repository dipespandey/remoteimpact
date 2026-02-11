"""
Bulk re-crawl jobs with short/missing descriptions from their original sources.
"""
import time
from django.core.management.base import BaseCommand
from django.db.models.functions import Length
from jobs.models import Job
from jobs.services.crawlers import crawl_greenhouse_job, crawl_lever_job, crawl_ashby_job


class Command(BaseCommand):
    help = "Bulk re-crawl jobs with short descriptions from sources we have crawlers for"

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=str,
            help="Only recrawl jobs from this source (greenhouse, lever, ashby)",
        )
        parser.add_argument(
            "--min-desc-len",
            type=int,
            default=500,
            help="Recrawl jobs with descriptions shorter than this (default: 500)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of jobs to recrawl",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=1.0,
            help="Delay between requests in seconds (default: 1.0)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Just show what would be recrawled, don't actually do it",
        )

    def handle(self, *args, **options):
        crawlers = {
            "greenhouse": crawl_greenhouse_job,
            "lever": crawl_lever_job,
            "ashby": crawl_ashby_job,
        }

        sources = [options["source"]] if options["source"] else list(crawlers.keys())
        min_len = options["min_desc_len"]
        limit = options["limit"]
        delay = options["delay"]
        dry_run = options["dry_run"]

        # Find jobs needing recrawl
        jobs_to_recrawl = (
            Job.objects.filter(source__in=sources, is_active=True)
            .annotate(desc_len=Length("description"))
            .filter(desc_len__lt=min_len)
            .order_by("source", "id")
        )

        if limit:
            jobs_to_recrawl = jobs_to_recrawl[:limit]

        total = jobs_to_recrawl.count()
        self.stdout.write(f"\nFound {total} jobs to recrawl (desc < {min_len} chars)")

        if dry_run:
            for job in jobs_to_recrawl:
                self.stdout.write(f"  [{job.source}] {job.id}: {job.title[:60]}")
            self.stdout.write(self.style.WARNING("\nDry run - no changes made"))
            return

        success = 0
        failed = 0
        skipped = 0

        for i, job in enumerate(jobs_to_recrawl, 1):
            self.stdout.write(f"\n[{i}/{total}] {job.source} #{job.id}: {job.title[:50]}...")
            self.stdout.write(f"  URL: {job.application_url}")
            self.stdout.write(f"  Current desc: {len(job.description or '')} chars")

            crawler = crawlers.get(job.source)
            if not crawler:
                self.stdout.write(self.style.WARNING(f"  ⊘ No crawler for {job.source}"))
                skipped += 1
                continue

            try:
                # Mark for crawling
                job.raw_data = job.raw_data or {}
                job.raw_data["needs_crawling"] = True
                job.save(update_fields=["raw_data"])

                # Crawl
                result = crawler(job)
                if result:
                    result.save()
                    new_len = len(result.description or "")
                    self.stdout.write(self.style.SUCCESS(f"  ✓ Updated: {new_len} chars"))
                    
                    # Show what changed
                    if result.salary_min or result.salary_max:
                        self.stdout.write(f"    Salary: {result.salary_min}-{result.salary_max} {result.salary_currency}")
                    if result.experience_level:
                        self.stdout.write(f"    Experience: {result.experience_level}")
                    
                    success += 1
                else:
                    self.stdout.write(self.style.ERROR(f"  ✗ Crawl returned None"))
                    failed += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Error: {e}"))
                failed += 1

            # Rate limiting
            if i < total:
                time.sleep(delay)

        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Done! Success: {success}, Failed: {failed}, Skipped: {skipped}")
