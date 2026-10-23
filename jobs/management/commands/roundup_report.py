"""Aggregate acquisition reporting and explicit, private provider export."""
import json
import os
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from jobs.models import NewsletterSubscriber, RoundupSubscriber, RoundupDelivery, UserProfile
from jobs.services.roundup import active_subscribers


class Command(BaseCommand):
    help = "Report weekly roundup growth; optionally export only its confirmed active opt-ins. No network calls."

    def add_arguments(self, parser):
        parser.add_argument("--export", help="Explicit private JSON path; refuses overwrite. Does not upload or send.")

    def handle(self, *args, **options):
        qs = RoundupSubscriber.objects.all()
        active = active_subscribers()
        week = timezone.now() - timedelta(days=7)
        report = {"as_of": timezone.now().isoformat(), "weekly_roundup": {
            "audience_records": qs.count(),
            "requested": qs.filter(consent_basis__in=["weekly_request", "account_explicit"]).count(), "confirmed_active": active.count(),
            "pending": qs.filter(confirmed_at__isnull=True, unsubscribed_at__isnull=True, consent_basis="weekly_request").count(),
            "needs_weekly_consent": qs.filter(consent_basis__in=["legacy_account", "legacy_unknown"], unsubscribed_at__isnull=True).count(),
            "monthly_only": qs.filter(consent_basis="monthly_only", unsubscribed_at__isnull=True).count(),
            "unsubscribed": qs.filter(unsubscribed_at__isnull=False).count(),
            "confirmed_last_7_days": active.filter(confirmed_at__gte=week).count(),
            "confirmed_referred": active.filter(referred_by__isnull=False).count(),
            "by_consent_basis": list(qs.values("consent_basis").annotate(count=Count("pk"))),
            "by_source": list(active.values("source", "campaign").annotate(count=Count("pk")).order_by("-count")),
            "by_interest": list(active.values("topic").annotate(count=Count("pk")))},
            "send_claims_by_status": list(RoundupDelivery.objects.values("issue_week", "status").annotate(count=Count("pk"))),
            "separate_legacy_lists": {"monthly_product_records": NewsletterSubscriber.objects.filter(confirmed=True, unsubscribed=False).count(),
                "accounts_with_digest_preference": UserProfile.objects.filter(email_newsletter=True, email_newsletter_unsubscribed_at__isnull=True).count()},
            "limits": ["Lists overlap. Do not sum them or treat legacy consent as weekly roundup permission.",
                "Confirmation is not delivery, a return visit or a job application.",
                "Source labels are self-reported attribution, not proof of incremental growth.",
                "Weekly sends require a current source-reviewed manifest and exact approval hash. Legacy defaults and monthly-only lists are excluded."]}
        if options["export"]:
            try:
                fd = os.open(options["export"], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except OSError as exc:
                raise CommandError(str(exc)) from exc
            with os.fdopen(fd, "w") as out:
                json.dump([{"email": sub.email, "topic": sub.topic, "source": sub.source,
                    "campaign": sub.campaign, "consent_version": sub.consent_version,
                    "consent_at": sub.consent_at.isoformat(), "confirmed_at": sub.confirmed_at.isoformat()}
                    for sub in active.iterator()], out, indent=2)
            report["exported_active_records"] = active.count()
        self.stdout.write(json.dumps(report, indent=2))
