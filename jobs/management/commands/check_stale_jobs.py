"""
Check for stale jobs that have been removed from source platforms.
Marks them as inactive if the source URL returns 404/410 (dead link).
"""
import time
import requests
from django.core.management.base import BaseCommand
from django.utils import timezone
from jobs.models import Job


class Command(BaseCommand):
    help = "Check jobs against source platforms and mark stale ones inactive"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Maximum number of jobs to check (default: 200)",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.5,
            help="Delay between requests in seconds (default: 0.5)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Just check, don't mark inactive",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        delay = options["delay"]
        dry_run = options["dry_run"]

        # Sources we can verify (have direct job URLs)
        verifiable_sources = [
            "greenhouse", "lever", "ashby", 
            "idealist", "reliefweb", "climatebase",
            "charityjob", "probablygood", "80000hours"
        ]

        # Get jobs from verifiable sources that are active
        # Order by updated_at to check oldest jobs first
        jobs = (
            Job.objects.filter(
                source__in=verifiable_sources,
                is_active=True,
            )
            .order_by("updated_at")[:limit]
        )

        total = jobs.count()
        self.stdout.write(f"Checking {total} jobs for dead links...")

        checked = 0
        marked_inactive = 0
        errors = 0
        by_source = {}  # Track inactive by source

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        for job in jobs:
            url = job.application_url
            if not url:
                continue

            try:
                # HEAD request to check if URL exists
                response = requests.head(
                    url, 
                    headers=headers, 
                    timeout=10, 
                    allow_redirects=True
                )
                
                checked += 1
                
                # Only mark inactive for definitive dead links
                if response.status_code in [404, 410]:
                    if not dry_run:
                        job.is_active = False
                        job.raw_data = job.raw_data or {}
                        job.raw_data["crawl_error"] = f"Dead link: {response.status_code}"
                        job.raw_data["last_stale_check"] = timezone.now().isoformat()
                        job.save()
                    marked_inactive += 1
                    by_source[job.source] = by_source.get(job.source, 0) + 1
                    self.stdout.write(f"  ✗ [{job.source}] {job.title[:50]}... ({response.status_code})")
                else:
                    # Job still exists - update check timestamp
                    if not dry_run:
                        job.raw_data = job.raw_data or {}
                        job.raw_data["last_stale_check"] = timezone.now().isoformat()
                        job.save(update_fields=["raw_data"])

            except requests.Timeout:
                errors += 1
                # Don't mark inactive on timeout - might be temporary
                if not dry_run:
                    job.raw_data = job.raw_data or {}
                    job.raw_data["last_stale_check"] = timezone.now().isoformat()
                    job.raw_data["last_stale_check_error"] = "timeout"
                    job.save(update_fields=["raw_data"])
            except requests.RequestException as e:
                errors += 1
                if not dry_run:
                    job.raw_data = job.raw_data or {}
                    job.raw_data["last_stale_check"] = timezone.now().isoformat()
                    job.raw_data["last_stale_check_error"] = str(e)[:100]
                    job.save(update_fields=["raw_data"])

            time.sleep(delay)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Checked: {checked}, Dead links removed: {marked_inactive}, Errors: {errors}"
        ))
        
        if by_source:
            self.stdout.write("\nBy source:")
            for source, count in sorted(by_source.items(), key=lambda x: -x[1]):
                self.stdout.write(f"  {source}: {count}")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("(Dry run - no changes made)"))
