"""
Remove expired jobs:
1. Jobs past their deadline (expires_at)
2. Jobs with no deadline but posted more than 180 days ago
Exception: Future of Life Institute rolling applications
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from datetime import timedelta
from jobs.models import Job


class Command(BaseCommand):
    help = "Remove expired jobs (past deadline or >180 days old without deadline)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=180,
            help="Max days for jobs without deadline (default: 180)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Just show what would be removed, don't save",
        )

    def handle(self, *args, **options):
        max_days = options["days"]
        dry_run = options["dry_run"]
        now = timezone.now()
        cutoff_date = now - timedelta(days=max_days)

        # Exceptions - keep these regardless of age
        # Future of Life Institute rolling applications
        exceptions = [
            "future of life",
            "futureoflife",
        ]

        self.stdout.write(f"Checking for expired jobs (deadline passed or >{max_days} days old)...")

        # Jobs past their deadline
        past_deadline = Job.objects.filter(
            is_active=True,
            expires_at__lt=now
        )

        # Jobs without deadline, posted more than X days ago
        old_no_deadline = Job.objects.filter(
            is_active=True,
            expires_at__isnull=True,
            posted_at__lt=cutoff_date
        )

        # Combine
        expired_jobs = (past_deadline | old_no_deadline).distinct()

        # Filter out exceptions
        for exception in exceptions:
            expired_jobs = expired_jobs.exclude(
                Q(organization__name__icontains=exception) |
                Q(title__icontains=exception)
            )

        total = expired_jobs.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No expired jobs found!"))
            return

        self.stdout.write(f"Found {total} expired jobs to remove:")

        # Show breakdown
        deadline_count = past_deadline.exclude(
            Q(organization__name__icontains="future of life") |
            Q(title__icontains="future of life")
        ).count()
        
        old_count = old_no_deadline.exclude(
            Q(organization__name__icontains="future of life") |
            Q(title__icontains="future of life")
        ).count()
        
        self.stdout.write(f"  - Past deadline: {deadline_count}")
        self.stdout.write(f"  - No deadline, >{max_days} days old: {old_count}")

        # Show samples
        for job in expired_jobs[:10]:
            age = (now - job.posted_at).days if job.posted_at else "?"
            reason = f"deadline {job.expires_at.date()}" if job.expires_at else f"{age} days old"
            self.stdout.write(f"  ✗ [{job.source}] {job.title[:40]}... ({reason})")

        if total > 10:
            self.stdout.write(f"  ... and {total - 10} more")

        if not dry_run:
            # Mark as inactive (soft delete)
            updated = expired_jobs.update(
                is_active=False,
                raw_data={"expired_reason": "auto_cleanup", "expired_at": now.isoformat()}
            )
            self.stdout.write(self.style.SUCCESS(f"\nMarked {updated} jobs as inactive"))
        else:
            self.stdout.write(self.style.WARNING("\n(Dry run - no changes made)"))
