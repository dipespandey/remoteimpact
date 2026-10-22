"""
Send job alert emails to users with matching new jobs.

Run daily for daily alerts, and weekly (on Mondays) for weekly alerts.
Uses Resend for email delivery.
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
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
            help="Ignore the send interval, but never resend already delivered jobs",
        )

    def handle(self, *args, **options):
        from jobs.services import embedding_service
        embedding_service.load_for_batch()
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

        # Snapshot IDs only. Every worker rechecks eligibility under the row lock.
        for alert_id in list(alerts.values_list("pk", flat=True)):
            try:
                outcome, delivered = self._process_alert(
                    alert_id, frequencies, dry_run=dry_run, force=force,
                )
                if outcome == "sent":
                    sent_count += 1
                    if not dry_run:
                        # Optional metrics must not undo accepted-mail bookkeeping.
                        from jobs.services import job_stats
                        for job in delivered:
                            try:
                                job_stats.record(job.pk, "scout_deliveries", dedupe=False)
                            except Exception:
                                logger.exception("Could not record Scout delivery metric for alert %s", alert_id)
                elif outcome == "error":
                    error_count += 1
                    self.stdout.write(self.style.WARNING(f"  Failed to send Scout #{alert_id}"))
                else:
                    skipped_count += 1
            except Exception:
                error_count += 1
                logger.exception("Error processing Scout #%s", alert_id)
                self.stdout.write(self.style.ERROR(f"  Error processing Scout #{alert_id}"))

        # Summary
        self.stdout.write("")
        status = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{status}Done: Sent {sent_count}, Skipped {skipped_count}, Errors {error_count}"
            )
        )


    def _process_alert(self, alert_id, frequencies, *, dry_run, force):
        """Serialize overlapping runs and persist receipts before optional metrics.

        The row lock covers the final due check, matching, provider submission and
        receipt writes. A second worker skips a busy alert, or sees its updated
        timestamp and delivery ledger on its next pass. Provider acceptance is
        still not proof of inbox delivery.
        """
        with transaction.atomic():
            alert = (JobAlert.objects.select_for_update(skip_locked=True)
                     .filter(pk=alert_id, is_active=True, frequency__in=frequencies).first())
            if alert is None:
                return "skipped", []
            wait = timedelta(minutes=4) if alert.frequency == "instant" else timedelta(hours=20)
            if not force and alert.last_sent_at and alert.last_sent_at >= timezone.now() - wait:
                return "skipped", []
            subscription = AssistantSubscription.get_or_create_for_user(alert.user)
            if not subscription.is_pro_active():
                return "skipped", []

            jobs = list(alert.get_matching_jobs()[:10])
            # Defense in depth if a stale candidate list bypasses matcher dedup.
            delivered_ids = set(alert.deliveries.filter(job_id__in=[job.pk for job in jobs])
                                .values_list("job_id", flat=True))
            seen = set(delivered_ids)
            pending = []
            for job in jobs:
                if job.pk not in seen:
                    seen.add(job.pk)
                    pending.append(job)
            if not pending:
                return "skipped", []
            if dry_run:
                self.stdout.write(f"[DRY RUN] Would send Scout #{alert.pk}: {len(pending)} new jobs")
                return "sent", pending
            if not email_service.send_job_alert(alert, pending):
                return "error", []
            JobAlertDelivery.objects.bulk_create([
                JobAlertDelivery(alert=alert, job=job,
                                 match_score=getattr(job, "scout_score", 0),
                                 match_reasons=getattr(job, "scout_reasons", []))
                for job in pending
            ])
            alert.last_sent_at = timezone.now()
            alert.save(update_fields=["last_sent_at"])
            return "sent", pending


# Need this import for the Q filter
from django.db import models
