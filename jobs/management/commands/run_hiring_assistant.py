"""Hiring assistant jobs for paid posts.

  --screen      read new applications against the role's must-haves (no email)
  --sourcing    find newly matching candidates and draft invitations; with --send, email each
                employer one digest of the new ones (they review and send invitations themselves)

Without --send nothing is emailed; the sourcing run prints what it would send.
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from jobs.models import Application, Job, SourcingSuggestion, TalentInvitation
from jobs.services import hiring_assistant
from jobs.services.candidate_suggestions import suggested_candidates
from jobs.services.email_service import email_service


MIN_EVIDENCE = 2


def live_paid_jobs():
    return [job for job in Job.objects.filter(is_paid=True, is_active=True).select_related("organization", "poster")
            if job.is_available]


class Command(BaseCommand):
    help = "Screen applications and source candidates for paid job posts."

    def add_arguments(self, parser):
        parser.add_argument("--screen", action="store_true")
        parser.add_argument("--sourcing", action="store_true")
        parser.add_argument("--send", action="store_true", help="Email sourcing digests (otherwise print only).")
        parser.add_argument("--per-job", type=int, default=5)

    def handle(self, *args, **options):
        from jobs.services import embedding_service
        embedding_service.load_for_batch()
        if options["screen"]:
            self.screen()
        if options["sourcing"]:
            self.sourcing(options["send"], options["per_job"])

    def screen(self):
        pending = Application.objects.filter(job__is_paid=True, screening={}).select_related(
            "job", "job__organization", "applicant")[:50]
        done = 0
        for application in pending:
            if hiring_assistant.screen_and_save(application) is not None:
                done += 1
        for job in live_paid_jobs():
            hiring_assistant.ensure_must_haves(job)
        self.stdout.write(f"Screened {done} application(s).")

    def sourcing(self, send, per_job):
        site = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
        for job in live_paid_jobs():
            already = set(SourcingSuggestion.objects.filter(job=job).values_list("seeker_id", flat=True))
            already |= set(TalentInvitation.objects.filter(job=job).values_list("seeker_id", flat=True))
            created = []
            for suggestion in suggested_candidates(job, limit=20):
                if suggestion.profile.pk in already or len(created) >= per_job:
                    continue
                # Suggest someone only when their own profile shows at least two of the role's must-haves.
                evidence = hiring_assistant.sourcing_evidence(job, suggestion.profile)
                if len(evidence) < MIN_EVIDENCE:
                    continue
                created.append(SourcingSuggestion.objects.create(
                    job=job, seeker=suggestion.profile, reasons=evidence,
                    draft_message=hiring_assistant.draft_invite(job, evidence)))
            unsent = list(SourcingSuggestion.objects.filter(job=job, notified_at__isnull=True)
                          .select_related("seeker", "seeker__user"))
            recipients = sorted({e for e in list(job.organization.members.values_list("email", flat=True))
                                 + [getattr(job.poster, "email", "")] if e})
            self.stdout.write(f"{job.title} ({job.organization.name}): {len(created)} new, {len(unsent)} to send -> {', '.join(recipients) or 'nobody'}")
            if not unsent or not recipients or not send:
                continue
            context = {
                "job": job,
                "suggestions": unsent,
                "invite_base": site,
                "dashboard_url": f"{site}{reverse('jobs:account')}",
                "candidates_url": f"{site}{reverse('jobs:employer_shortlist', kwargs={'slug': job.slug})}",
            }
            subject = f"{len(unsent)} {'person' if len(unsent) == 1 else 'people'} who fit your {job.title} role"
            sent = False
            for email in recipients:
                sent = email_service.send_email(
                    to=email, subject=subject,
                    html=render_to_string("emails/sourcing_digest.html", context),
                    text=render_to_string("emails/sourcing_digest.txt", context)) or sent
            if sent:
                SourcingSuggestion.objects.filter(pk__in=[s.pk for s in unsent]).update(notified_at=timezone.now())
