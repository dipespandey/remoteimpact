"""Remind people who asked for the weekly roundup but never clicked the confirm link, once.

About half of signups never confirm, and most who do confirm within minutes. One reminder,
sent a day after the request, with a fresh link. Nobody is subscribed until they confirm,
and nobody gets a second reminder.

Dry run by default. Pass --send.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.template.loader import render_to_string
from django.utils import timezone

from jobs.management.commands.send_roundup_reconsent import opted_out_emails
from jobs.models import OutreachSend, RoundupSubscriber
from jobs.services.email_service import email_service
from jobs.services.roundup import public_url, token_for

CAMPAIGN = "roundup-confirm-reminder"


def candidates(now=None):
    now = now or timezone.now()
    reminded = set(OutreachSend.objects.filter(campaign=CAMPAIGN).values_list("email", flat=True))
    excluded = reminded | opted_out_emails()
    rows = RoundupSubscriber.objects.filter(
        consent_basis="weekly_request", confirmed_at__isnull=True, unsubscribed_at__isnull=True,
        confirmation_requested_at__lte=now - timedelta(hours=20),
        confirmation_requested_at__gte=now - timedelta(days=7),
    ).order_by("confirmation_requested_at")
    return [row for row in rows if row.email.lower() not in excluded]


class Command(BaseCommand):
    help = "Send one confirm reminder to weekly roundup requests left unconfirmed. Dry run unless --send."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true")

    def handle(self, *args, **options):
        rows = candidates()
        if not options["send"]:
            for row in rows:
                self.stdout.write(f"Would remind {row.email} (requested {row.confirmation_requested_at:%Y-%m-%d %H:%M})")
            self.stdout.write(f"Dry run: {len(rows)} eligible.")
            return
        sent = 0
        for row in rows:
            try:
                OutreachSend.objects.create(campaign=CAMPAIGN, email=row.email.lower())
            except IntegrityError:
                continue
            confirm = public_url("jobs:roundup_confirm", token=token_for(row, "confirm"))
            stop = public_url("jobs:roundup_unsubscribe", token=token_for(row, "unsubscribe"))
            accepted = email_service.send_email(
                to=row.email,
                subject="One click to start your weekly job picks",
                html=render_to_string("emails/roundup_confirm.html", {"confirm_url": confirm, "stop_url": stop}),
                text=(f"You asked for Remote Impact's weekly job picks but haven't confirmed yet. Confirm here: {confirm}\n"
                      f"This is the only reminder we'll send. Stop requests: {stop}"),
                unsubscribe_url=stop,
            )
            if not accepted:
                OutreachSend.objects.filter(campaign=CAMPAIGN, email=row.email.lower()).delete()
                continue
            sent += 1
        self.stdout.write(f"Sent {sent} of {len(rows)} confirm reminders.")
