"""Reinterpret recent empty Scouts, preserving a report before any changes."""

import json
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from jobs.models import ConsumerFunnelEvent, JobAlert, SeekerProfile
from jobs.services.job_scout_service import JobScoutService
from jobs.views.impact_wizard import profile_scout_intent


class Command(BaseCommand):
    help = "Replay empty Scout briefs; optionally repair saved preferences without sending email."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument("--alert-id", type=int)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--report", required=True)

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=options["days"])
        alert_ids = ConsumerFunnelEvent.objects.filter(
            event=ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY,
            created_at__gte=since,
        ).values("alert_id")
        alerts = JobAlert.objects.filter(
            pk__in=alert_ids, deliveries__isnull=True,
        ).distinct().order_by("id")
        if options["alert_id"]:
            alerts = alerts.filter(pk=options["alert_id"])
        parsed_cache = {}
        changes = []
        report = []
        for alert in alerts:
            intent = alert.intent_text
            if len(intent) == 400 and alert.funnel_events.filter(
                event=ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY,
                metadata__source="profile_complete",
            ).exists():
                profile = SeekerProfile.objects.filter(user_id=alert.user_id).first()
                if profile:
                    complete = profile_scout_intent(profile)
                    if complete.startswith(intent):
                        intent = complete
            if intent not in parsed_cache:
                parsed_cache[intent] = JobScoutService.parse_intent(intent)
            preferences = parsed_cache[intent]
            matches = JobScoutService.get_matches_for_preferences(
                preferences, since=timezone.now() - timedelta(days=30), alert=alert, limit=5,
            )
            report.append({
                "alert_id": alert.pk,
                "previous_intent": alert.intent_text,
                "new_intent": intent,
                "previous_preferences": alert.parsed_preferences,
                "new_preferences": preferences,
                "matches": [{"id": job.pk, "title": job.title, "score": job.scout_score}
                            for job in matches],
            })
            changes.append((alert, preferences, intent))
            self.stdout.write(f"Scout {alert.pk}: {len(matches)} matches; titles={preferences['titles']}")
        Path(options["report"]).write_text(json.dumps(report, indent=2), encoding="utf-8")
        repaired = 0
        if options["apply"]:
            with transaction.atomic():
                for alert, preferences, intent in changes:
                    current = JobAlert.objects.select_for_update().get(pk=alert.pk)
                    if current.intent_text != alert.intent_text or current.deliveries.exists():
                        raise CommandError("Scout changed during replay; no repairs were applied.")
                    current.parsed_preferences = preferences
                    current.intent_text = intent
                    current.keywords = ", ".join(
                        preferences["titles"] + preferences["skills"] + preferences["include_keywords"]
                    )[:2000]
                    current.save(update_fields=["parsed_preferences", "intent_text", "keywords", "updated_at"])
                    current.categories.set(current.categories.model.objects.filter(
                        slug__in=preferences["category_slugs"]
                    ))
                    repaired += 1
        recovered = sum(bool(item["matches"]) for item in report)
        self.stdout.write(f"Replayed {len(report)} Scouts; {recovered} have matches; repaired {repaired}. No emails sent.")
