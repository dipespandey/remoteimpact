"""
Check for stale jobs that have been removed from source platforms.
Marks them as inactive.
"""
import time
import requests
from django.core.management.base import BaseCommand
from django.db.models import Q
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

        # Sources we can verify
        verifiable_sources = [
            "greenhouse", "lever", "ashby", 
            "idealist", "reliefweb", "climatebase",
            "charityjob", "probablygood"
        ]

        # Get jobs that haven't been checked recently
        # Prioritize jobs not checked in the last 24 hours
        one_day_ago = timezone.now() - timezone.timedelta(days=1)
        
        jobs = (
            Job.objects.filter(
                source__in=verifiable_sources,
                is_active=True,
            )
            .exclude(
                raw_data__last_stale_check__gte=one_day_ago.isoformat()
            )
            .order_by("updated_at")[:limit]
        )

        total = jobs.count()
        self.stdout.write(f"Checking {total} jobs for staleness...")

        checked = 0
        marked_inactive = 0
        errors = 0

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; RemoteImpact/1.0; +https://remoteimpact.org)",
        }

        for job in jobs:
            url = job.application_url
            if not url:
                continue

            try:
                # Just do a HEAD request to check if URL exists
                response = requests.head(
                    url, 
                    headers=headers, 
                    timeout=10, 
                    allow_redirects=True
                )
                
                checked += 1

                if response.status_code in [404, 410, 403]:
                    # Job is gone
                    if not dry_run:
                        job.is_active = False
                        job.raw_data = job.raw_data or {}
                        job.raw_data["crawl_error"] = f"Stale check: {response.status_code}"
                        job.raw_data["last_stale_check"] = timezone.now().isoformat()
                        job.save()
                    marked_inactive += 1
                    self.stdout.write(f"  ✗ [{job.source}] {job.title[:40]}... ({response.status_code})")
                else:
                    # Job still exists - update check timestamp
                    if not dry_run:
                        job.raw_data = job.raw_data or {}
                        job.raw_data["last_stale_check"] = timezone.now().isoformat()
                        job.save(update_fields=["raw_data"])

            except requests.RequestException as e:
                errors += 1
                # Don't mark as inactive on network errors
                if not dry_run:
                    job.raw_data = job.raw_data or {}
                    job.raw_data["last_stale_check"] = timezone.now().isoformat()
                    job.raw_data["last_stale_check_error"] = str(e)[:100]
                    job.save(update_fields=["raw_data"])

            time.sleep(delay)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Checked: {checked}, Marked inactive: {marked_inactive}, Errors: {errors}"
        ))
        
        if dry_run:
            self.stdout.write(self.style.WARNING("(Dry run - no changes made)"))
