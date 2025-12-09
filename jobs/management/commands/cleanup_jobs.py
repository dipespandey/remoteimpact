"""
Management command to clean up old/expired jobs.

Usage:
    python manage.py cleanup_jobs                    # Deactivate expired and stale jobs
    python manage.py cleanup_jobs --days 60         # Custom staleness threshold
    python manage.py cleanup_jobs --dry-run         # Preview without making changes
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import Job


class Command(BaseCommand):
    help = "Deactivate expired and stale job listings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=45,
            help="Deactivate jobs not updated in N days (default: 45).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without making them.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        now = timezone.now()
        cutoff = now - timedelta(days=days)

        # Find expired jobs (past expires_at date)
        expired_jobs = Job.objects.filter(
            is_active=True,
            expires_at__lt=now,
        )
        expired_count = expired_jobs.count()

        # Find stale jobs (not updated recently and no expiry set)
        stale_jobs = Job.objects.filter(
            is_active=True,
            expires_at__isnull=True,
            updated_at__lt=cutoff,
        )
        stale_count = stale_jobs.count()

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))
            self.stdout.write(f"Would deactivate {expired_count} expired jobs")
            self.stdout.write(f"Would deactivate {stale_count} stale jobs (not updated in {days} days)")
        else:
            # Deactivate expired jobs
            if expired_count > 0:
                expired_jobs.update(is_active=False)
                self.stdout.write(
                    self.style.SUCCESS(f"Deactivated {expired_count} expired jobs")
                )

            # Deactivate stale jobs
            if stale_count > 0:
                stale_jobs.update(is_active=False)
                self.stdout.write(
                    self.style.SUCCESS(f"Deactivated {stale_count} stale jobs (not updated in {days} days)")
                )

            if expired_count == 0 and stale_count == 0:
                self.stdout.write("No jobs to deactivate")

        # Show remaining active jobs
        active_count = Job.objects.filter(is_active=True).count()
        self.stdout.write(f"\nActive jobs remaining: {active_count}")
