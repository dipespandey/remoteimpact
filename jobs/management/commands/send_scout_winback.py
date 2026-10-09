"""One-time email to people whose free Scout ran once, inviting them to keep it running.

Dry run by default: prints the audience. Pass --send (and optionally --limit) to send.
Each person gets this email at most once; unsubscribed and Pro users are skipped.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from jobs.models import AssistantSubscription, ConsumerFunnelEvent, JobAlert, UserProfile
from jobs.services.email_service import email_service

CE = ConsumerFunnelEvent.Event


class Command(BaseCommand):
    help = "Invite free Scout users to keep their search running with Pro (once each). Dry run unless --send."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true")
        parser.add_argument("--limit", type=int, default=0, help="Send to at most this many people.")
        parser.add_argument("--min-age-days", type=int, default=1, help="Skip Scouts created more recently.")

    def handle(self, *args, **options):
        site = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
        cutoff = timezone.now() - timedelta(days=options["min_age_days"])
        user_ids = (
            JobAlert.objects.filter(user__is_active=True, created_at__lte=cutoff)
            .values("user_id").annotate(last=Max("created_at")).order_by("-last").values_list("user_id", flat=True)
        )
        already = set(ConsumerFunnelEvent.objects.filter(event=CE.WINBACK_SENT).values_list("user_id", flat=True))
        unsubscribed = set(UserProfile.objects.filter(
            email_newsletter_unsubscribed_at__isnull=False).values_list("user_id", flat=True))
        sent = skipped = 0
        for user_id in user_ids:
            if user_id in already or user_id in unsubscribed:
                skipped += 1
                continue
            alert = JobAlert.objects.filter(user_id=user_id).select_related("user").order_by("-created_at").first()
            user = alert.user
            if not user.email or AssistantSubscription.get_or_create_for_user(user).is_pro_active():
                skipped += 1
                continue
            brief = (alert.name or alert.intent_text or "").strip().replace("\n", " ")
            brief = brief[:140] + ("..." if len(brief) > 140 else "")
            resume_url = f"{site}{reverse('jobs:job_scout_subscribe')}?source=winback_email"
            if not options["send"]:
                self.stdout.write(f"Would email {user.email}: {brief[:60]!r}")
            elif email_service.send_scout_winback(user, brief=brief, resume_url=resume_url):
                ConsumerFunnelEvent.objects.create(user=user, event=CE.WINBACK_SENT,
                                                   dedupe_key=f"winback:{user.pk}")
            else:
                continue
            sent += 1
            if options["limit"] and sent >= options["limit"]:
                break
        verb = "Sent" if options["send"] else "Dry run: would send"
        self.stdout.write(f"{verb} {sent} email(s); skipped {skipped}.")
