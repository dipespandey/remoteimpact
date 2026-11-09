"""Ask legacy audience members to confirm the weekly roundup, once.

Legacy rows (an old account checkbox or an unknown source) never confirmed weekly email, so the
roundup skips them. This sends each one a single confirmation request. Nobody is subscribed until
they click and confirm, and nobody who ignores it hears from us again.

Dry run by default. Pass --send, with --limit for batches.
"""
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.template.loader import render_to_string
from django.utils import timezone

from jobs.models import NewsletterSubscriber, OutreachSend, RoundupSubscriber, UserProfile
from jobs.services.email_service import email_service
from jobs.services.roundup import public_url, token_for

CAMPAIGN = "roundup-reconsent-2026-11"
LEGACY = ("legacy_account", "legacy_unknown")


def opted_out_emails():
    emails = set(NewsletterSubscriber.objects.filter(unsubscribed=True).values_list("email", flat=True))
    emails |= set(UserProfile.objects.filter(email_newsletter_unsubscribed_at__isnull=False)
                  .values_list("user__email", flat=True))
    return {e.lower() for e in emails if e}


def candidates():
    done = set(OutreachSend.objects.filter(campaign=CAMPAIGN).values_list("email", flat=True))
    excluded = done | opted_out_emails()
    rows = RoundupSubscriber.objects.filter(consent_basis__in=LEGACY, unsubscribed_at__isnull=True).order_by("created_at")
    return [row for row in rows if row.email.lower() not in excluded]


class Command(BaseCommand):
    help = "Send legacy audience members one request to confirm the weekly roundup. Dry run unless --send."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        rows = candidates()
        batch = rows[: options["limit"]] if options["limit"] else rows
        if not options["send"]:
            for row in batch:
                self.stdout.write(f"Would email {row.email} ({row.consent_basis})")
            self.stdout.write(f"Dry run: {len(batch)} of {len(rows)} eligible.")
            return
        sent = 0
        for row in batch:
            try:
                OutreachSend.objects.create(campaign=CAMPAIGN, email=row.email.lower())
            except IntegrityError:
                continue
            RoundupSubscriber.objects.filter(pk=row.pk).update(confirmation_requested_at=timezone.now())
            context = {"confirm_url": public_url("jobs:roundup_confirm", token=token_for(row, "confirm")),
                       "stop_url": public_url("jobs:roundup_unsubscribe", token=token_for(row, "unsubscribe")),
                       "signup_url": public_url("jobs:roundup")}
            accepted = email_service.send_email(
                to=row.email,
                subject="Do you still want Remote Impact job picks?",
                html=render_to_string("emails/roundup_reconsent.html", context),
                text=render_to_string("emails/roundup_reconsent.txt", context),
                unsubscribe_url=context["stop_url"],
            )
            if not accepted:
                OutreachSend.objects.filter(campaign=CAMPAIGN, email=row.email.lower()).delete()
                continue
            sent += 1
        self.stdout.write(f"Sent {sent} of {len(batch)}; {len(rows) - len(batch)} left for later batches.")
