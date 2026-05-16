"""
Management command to deactivate jobs that have passed their expiration date
or are stale imported jobs with no known expiration date.

Usage:
    python manage.py deactivate_expired_jobs --dry-run
    python manage.py deactivate_expired_jobs
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from jobs.models import Job


class Command(BaseCommand):
    help = "Deactivate jobs that are expired or stale with no known expiration date"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deactivated without making changes",
        )
        parser.add_argument(
            "--stale-days",
            type=int,
            default=180,
            help="Deactivate active jobs without expires_at after this many days",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()
        stale_cutoff = now - timedelta(days=options["stale_days"])

        # Find jobs that should no longer be public/indexable.
        expired_jobs = Job.objects.filter(
            is_active=True,
            expires_at__lt=now,
        )
        stale_jobs = Job.objects.filter(
            is_active=True,
            expires_at__isnull=True,
            posted_at__lt=stale_cutoff,
        )

        count = expired_jobs.count() + stale_jobs.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No expired or stale jobs to deactivate."))
            return

        self.stdout.write(
            f"Found {count} jobs to deactivate "
            f"({expired_jobs.count()} expired, {stale_jobs.count()} stale):"
        )

        # Show sample
        sample_jobs = list(expired_jobs[:5]) + list(stale_jobs[:5])
        for job in sample_jobs:
            reason = (
                f"expired: {job.expires_at.date()}"
                if job.expires_at
                else f"posted: {job.posted_at.date()}, no expiry"
            )
            self.stdout.write(
                f"  - {job.title[:50]} ({reason}, source: {job.source})"
            )

        if count > len(sample_jobs):
            self.stdout.write(f"  ... and {count - len(sample_jobs)} more")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("\n[DRY RUN] No changes were made to database")
            )
        else:
            updated = expired_jobs.update(is_active=False)
            updated += stale_jobs.update(is_active=False)
            self.stdout.write(
                self.style.SUCCESS(f"\nDeactivated {updated} expired or stale jobs.")
            )
