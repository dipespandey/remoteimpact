"""
Send job alert emails to users with matching new jobs.

Run daily for daily alerts, and weekly (on Mondays) for weekly alerts.
Uses Resend for email delivery.
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import AssistantSubscription, JobAlert, JobAlertDelivery
from jobs.services.email_service import email_service

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send job alert emails to users with matching new jobs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--frequency",
            choices=["instant", "daily", "weekly"],
            default=None,
            help="Only send alerts with this frequency (default: auto based on day)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be sent without sending",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send even if already sent today",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        frequency = options["frequency"]
        force = options["force"]

        # Auto-detect: daily always, weekly only on Mondays
        if frequency is None:
            frequencies = ["daily"]
            if timezone.now().weekday() == 0:  # Monday
                frequencies.append("weekly")
        else:
            frequencies = [frequency]

        self.stdout.write(f"Processing {', '.join(frequencies)} alerts...")

        # Get active alerts for the selected frequencies
        alerts = JobAlert.objects.filter(
            is_active=True,
            frequency__in=frequencies,
        ).select_related("user")

        # Optionally filter out recently sent
        if not force:
            wait = timedelta(minutes=4) if frequencies == ["instant"] else timedelta(hours=20)
            cutoff = timezone.now() - wait
            alerts = alerts.filter(
                models.Q(last_sent_at__isnull=True) | models.Q(last_sent_at__lt=cutoff)
            )

        total_alerts = alerts.count()
        self.stdout.write(f"Found {total_alerts} alerts to process")

        sent_count = 0
        skipped_count = 0
        error_count = 0

        for alert in alerts:
            subscription = AssistantSubscription.get_or_create_for_user(alert.user)
            if not subscription.is_pro_active():
                skipped_count += 1
                if options["verbosity"] >= 2:
                    self.stdout.write(
                        f"  Skipped {alert.user.email}: recurring alerts require Pro"
                    )
                continue

            # Get matching jobs since last send
            jobs = list(alert.get_matching_jobs()[:10])
            
            if not jobs:
                skipped_count += 1
                if options["verbosity"] >= 2:
                    self.stdout.write(f"  Skipped {alert.user.email}: no matching jobs")
                continue

            if dry_run:
                self.stdout.write(
                    f"[DRY RUN] Would send to {alert.user.email}: "
                    f"{len(jobs)} jobs for alert '{alert.name or 'Unnamed'}'"
                )
                sent_count += 1
                continue

            # Send the email
            try:
                success = email_service.send_job_alert(alert, jobs)
                
                if success:
                    JobAlertDelivery.objects.bulk_create(
                        [
                            JobAlertDelivery(
                                alert=alert,
                                job=job,
                                match_score=getattr(job, "scout_score", 0),
                                match_reasons=getattr(job, "scout_reasons", []),
                            )
                            for job in jobs
                        ],
                        ignore_conflicts=True,
                    )
                    from jobs.services import job_stats
                    for delivered_job in jobs:
                        job_stats.record(delivered_job.pk, "scout_deliveries", dedupe=False)
                    alert.last_sent_at = timezone.now()
                    alert.save(update_fields=["last_sent_at"])
                    sent_count += 1
                    
                    if options["verbosity"] >= 2:
                        self.stdout.write(
                            self.style.SUCCESS(f"  Sent to {alert.user.email}: {len(jobs)} jobs")
                        )
                else:
                    error_count += 1
                    self.stdout.write(
                        self.style.WARNING(f"  Failed to send to {alert.user.email}")
                    )
            except Exception as e:
                error_count += 1
                logger.error(f"Error sending alert to {alert.user.email}: {e}")
                self.stdout.write(
                    self.style.ERROR(f"  Error sending to {alert.user.email}: {e}")
                )

        # Summary
        self.stdout.write("")
        status = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{status}Done: Sent {sent_count}, Skipped {skipped_count} (no jobs), Errors {error_count}"
            )
        )


# Need this import for the Q filter
from django.db import models
