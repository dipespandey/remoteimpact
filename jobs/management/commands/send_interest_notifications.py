from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from jobs.models import Job, JobInterest
from jobs.services.email_service import email_service
from jobs.services.shortlists import visible_interests


class Command(BaseCommand):
    help = "Notify paid job posters about new opt-in candidates (at most once per role per day). Run under flock."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        now = timezone.now()
        pending = JobInterest.objects.filter(share_with_employer=True, employer_notified_at__isnull=True)
        jobs = Job.objects.filter(pk__in=pending.values("job_id"), is_paid=True,
                                  is_active=True, poster__isnull=False).exclude(expires_at__lt=now).select_related("poster")
        sent = 0
        for job in jobs.iterator():
            if job.candidate_interests.filter(employer_notified_at__gte=now - timedelta(days=1)).exists():
                continue
            ids = list(visible_interests(job).filter(employer_notified_at__isnull=True).values_list("pk", flat=True))
            if not ids or not job.poster.email:
                continue
            if options["dry_run"]:
                self.stdout.write(f"Job {job.pk}: {len(ids)} new opt-in candidates")
                continue
            url = settings.SITE_URL.rstrip("/") + reverse("jobs:employer_shortlist", args=[job.slug])
            html = format_html('<p>New candidates have chosen to share their details for <strong>{}</strong>.</p>'
                               '<p><a href="{}">Review your private candidate shortlist</a></p>'
                               '<p>Contact them only about this role. Candidate details are self-reported.</p>', job.title, url)
            if email_service.send_email(to=job.poster.email, subject="New interest in your Remote Impact role",
                                        html=str(html), text=f"New opt-in candidates for {job.title}. Review: {url}"):
                JobInterest.objects.filter(pk__in=ids).update(employer_notified_at=now)
                sent += 1
        self.stdout.write(f"Employer notifications sent: {sent}")
