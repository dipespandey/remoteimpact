"""Tell an employer what their free featured trial did, and offer to keep the role featured for $49.

Recipients are named one by one (--to org-slug=address) because each send is approved by hand.
A role that has closed, or that got no more clicks while featured, is skipped: the email only
goes out when its numbers make the case. Dry run unless --send; each address gets it once.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse

from jobs.models import OutreachSend
from jobs.services import feature_trials
from jobs.services.email_service import email_service
from jobs.services.job_service import JobService

CAMPAIGN = "trial-results-2026-11"


def recipient(value):
    slug, sep, email = value.partition("=")
    if not sep or "@" not in email:
        raise CommandError(f"--to takes org-slug=address, got {value!r}")
    return slug.strip(), email.strip().lower()


class Command(BaseCommand):
    help = "Email named trial employers their before/during numbers and a $49 offer. Dry run unless --send."

    def add_arguments(self, parser):
        parser.add_argument("--to", action="append", type=recipient, required=True, metavar="ORG_SLUG=EMAIL")
        parser.add_argument("--send", action="store_true")

    def handle(self, *args, **options):
        site = settings.SITE_URL.rstrip("/")
        trials = feature_trials.active_trials().select_related("job__organization")
        rows, site_ratio = feature_trials.results(trials)
        by_org = {row["trial"].job.organization.slug: row for row in rows}
        live = set(JobService.get_available_jobs().values_list("pk", flat=True))
        for slug, email in options["to"]:
            row = by_org.get(slug)
            if row is None:
                self.stdout.write(f"Skip {slug}: no running trial")
                continue
            job = row["trial"].job
            (before_views, before_clicks), (views, clicks) = row["before"], row["during"]
            if job.pk not in live or clicks <= before_clicks:
                self.stdout.write(f"Skip {slug}: {'role closed' if job.pk not in live else 'no lift'}")
                continue
            context = {
                "org": job.organization, "job": job, "days": row["days"], "ends": row["trial"].ends_at,
                "before_views": before_views, "before_clicks": before_clicks, "views": views, "clicks": clicks,
                "site_drop": round((1 - site_ratio) * 100) if site_ratio and site_ratio < 0.97 else None,
                "feature_url": f"{site}{reverse('jobs:feature_job', kwargs={'slug': job.slug})}?utm_source=email&utm_campaign={CAMPAIGN}",
            }
            subject = f"{job.organization.name}: {clicks} applicant clicks in {row['days']} days while featured"
            text = render_to_string("emails/trial_results.txt", context)
            if not options["send"]:
                self.stdout.write(f"Would email {email}\nSubject: {subject}\n{text}")
                continue
            try:
                with transaction.atomic():
                    OutreachSend.objects.create(campaign=CAMPAIGN, email=email, organization=job.organization)
            except IntegrityError:
                self.stdout.write(f"Skip {slug}: already sent")
                continue
            if not email_service.send_email(to=email, subject=subject, text=text,
                                            html=render_to_string("emails/trial_results.html", context)):
                OutreachSend.objects.filter(campaign=CAMPAIGN, email=email).delete()
                self.stdout.write(f"Failed {slug}: provider refused")
                continue
            self.stdout.write(f"Sent {slug} to {email}")
