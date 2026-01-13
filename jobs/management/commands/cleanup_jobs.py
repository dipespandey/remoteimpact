"""
Management command to clean up old/expired jobs.

Usage:
    python manage.py cleanup_jobs                    # Deactivate expired and old jobs
    python manage.py cleanup_jobs --max-age 180     # Custom max age in days (default: 180 = 6 months)
    python manage.py cleanup_jobs --dry-run         # Preview without making changes
    python manage.py cleanup_jobs --delete          # Delete instead of deactivate (use with caution)
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import Job


class Command(BaseCommand):
    help = "Deactivate or delete expired and old job listings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-age",
            type=int,
            default=180,
            help="Deactivate jobs posted more than N days ago (default: 180 = 6 months).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without making them.",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete jobs instead of deactivating them (use with caution).",
        )

    def handle(self, *args, **options):
        max_age = options["max_age"]
        dry_run = options["dry_run"]
        delete = options["delete"]
        now = timezone.now()
        cutoff = now - timedelta(days=max_age)

        action = "delete" if delete else "deactivate"

        # Find expired jobs (past expires_at date)
        expired_jobs = Job.objects.filter(
            is_active=True,
            expires_at__lt=now,
        )
        expired_count = expired_jobs.count()

        # Find old jobs (posted more than max_age days ago)
        old_jobs = Job.objects.filter(
            is_active=True,
            posted_at__lt=cutoff,
        )
        old_count = old_jobs.count()

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))
            self.stdout.write(f"Would {action} {expired_count} expired jobs (past expiry date)")
            self.stdout.write(f"Would {action} {old_count} old jobs (posted > {max_age} days ago)")
        else:
            # Process expired jobs
            if expired_count > 0:
                if delete:
                    expired_jobs.delete()
                    self.stdout.write(
                        self.style.SUCCESS(f"Deleted {expired_count} expired jobs")
                    )
                else:
                    expired_jobs.update(is_active=False)
                    self.stdout.write(
                        self.style.SUCCESS(f"Deactivated {expired_count} expired jobs")
                    )

            # Process old jobs
            if old_count > 0:
                if delete:
                    old_jobs.delete()
                    self.stdout.write(
                        self.style.SUCCESS(f"Deleted {old_count} old jobs (posted > {max_age} days ago)")
                    )
                else:
                    old_jobs.update(is_active=False)
                    self.stdout.write(
                        self.style.SUCCESS(f"Deactivated {old_count} old jobs (posted > {max_age} days ago)")
                    )

            if expired_count == 0 and old_count == 0:
                self.stdout.write("No jobs to process")

        # Show remaining active jobs
        active_count = Job.objects.filter(is_active=True).count()
        total_count = Job.objects.count()
        self.stdout.write(f"\nActive jobs: {active_count} / {total_count} total")
