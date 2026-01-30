import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from jobs.models import JobAlert

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send job alert emails to users with matching new jobs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--frequency",
            choices=["daily", "weekly"],
            default=None,
            help="Only send alerts with this frequency (default: auto based on day)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be sent without sending",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        frequency = options["frequency"]

        # Auto-detect: daily always, weekly only on Mondays
        if frequency is None:
            frequencies = ["daily"]
            if timezone.now().weekday() == 0:  # Monday
                frequencies.append("weekly")
        else:
            frequencies = [frequency]

        alerts = JobAlert.objects.filter(
            is_active=True,
            frequency__in=frequencies,
        ).select_related("user")

        sent_count = 0
        for alert in alerts:
            jobs = alert.get_matching_jobs()[:20]
            if not jobs:
                continue

            subject = f"🔔 {jobs.count()} new jobs matching your alert"
            if alert.name:
                subject += f' "{alert.name}"'

            # Build plain text email
            job_lines = []
            for job in jobs[:10]:
                job_lines.append(
                    f"• {job.title} at {job.organization.name}\n"
                    f"  {settings.SITE_URL}{job.get_absolute_url()}\n"
                )

            body = (
                f"Hi {alert.user.first_name or 'there'},\n\n"
                f"We found {jobs.count()} new job{'s' if jobs.count() != 1 else ''} matching your alert"
                f'{" \"" + alert.name + "\"" if alert.name else ""}:\n\n'
                + "\n".join(job_lines)
                + f"\n\nView all jobs: {settings.SITE_URL}/jobs/\n"
                f"Manage your alerts: {settings.SITE_URL}/alerts/\n\n"
                f"— Remote Impact Jobs"
            )

            if dry_run:
                self.stdout.write(f"[DRY RUN] Would send to {alert.user.email}: {subject} ({jobs.count()} jobs)")
            else:
                try:
                    send_mail(
                        subject=subject,
                        message=body,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[alert.user.email],
                        fail_silently=False,
                    )
                    alert.last_sent_at = timezone.now()
                    alert.save(update_fields=["last_sent_at"])
                    sent_count += 1
                except Exception as e:
                    logger.error(f"Failed to send alert to {alert.user.email}: {e}")

        self.stdout.write(self.style.SUCCESS(f"Sent {sent_count} job alert emails"))
