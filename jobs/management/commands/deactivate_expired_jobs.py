"""
Management command to deactivate jobs that have passed their expiration date.

Usage:
    python manage.py deactivate_expired_jobs --dry-run
    python manage.py deactivate_expired_jobs
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import Job


class Command(BaseCommand):
    help = "Deactivate jobs that have passed their expires_at date"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deactivated without making changes",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()

        # Find expired jobs that are still active
        expired_jobs = Job.objects.filter(
            is_active=True,
            expires_at__lt=now,
        )

        count = expired_jobs.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No expired jobs to deactivate."))
            return

        self.stdout.write(f"Found {count} expired jobs to deactivate:")

        # Show sample
        for job in expired_jobs[:10]:
            self.stdout.write(
                f"  - {job.title[:50]} (expired: {job.expires_at.date()}, source: {job.source})"
            )

        if count > 10:
            self.stdout.write(f"  ... and {count - 10} more")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("\n[DRY RUN] No changes were made to database")
            )
        else:
            updated = expired_jobs.update(is_active=False)
            self.stdout.write(
                self.style.SUCCESS(f"\nDeactivated {updated} expired jobs.")
            )
