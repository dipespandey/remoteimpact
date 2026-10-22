"""Diagnose recorded initial outcomes without rerunning matching or exporting briefs."""
import json
from collections import Counter, defaultdict
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from jobs.models import ConsumerFunnelEvent as E, JobAlert


def classify(event):
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    diagnostics = metadata.get("matching")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    if event.event == E.Event.INITIAL_EMAIL_FAILED:
        return "email_transport_failed"
    if event.event == E.Event.INITIAL_EMAIL_SENT:
        return "email_send_accepted"
    if diagnostics.get("review_incomplete") or diagnostics.get("empty_reason") == "review_incomplete":
        return "review_incomplete"
    if not diagnostics:
        return "empty_without_diagnostics"
    reason = diagnostics.get("empty_reason")
    if reason == "no_eligible_candidates":
        return "empty_no_eligible_candidates"
    if reason == "no_relevant_matches":
        return "empty_no_relevant_matches"
    return "empty_unclassified"


class Command(BaseCommand):
    help = "Unique new-Scout initial outcomes, retries and recorded rejection evidence; read-only and aggregate."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=28)
        parser.add_argument("--limit", type=int, default=10000)

    def handle(self, *args, **options):
        days, limit = options["days"], options["limit"]
        if not 1 <= days <= 366 or not 1 <= limit <= 50000:
            raise CommandError("days must be 1–366; limit 1–50000.")
        until = timezone.now()
        since = until - timedelta(days=days)
        internal = getattr(settings, "GROWTH_INTERNAL_USER_IDS", [])
        query = JobAlert.objects.filter(created_at__gte=since, created_at__lt=until)
        excluded = query.filter(user__is_staff=True).count()
        query = query.exclude(user__is_staff=True)
        excluded += query.filter(user_id__in=internal).count()
        query = query.exclude(user_id__in=internal)
        ids = list(query.order_by("created_at", "pk").values_list("pk", flat=True)[:limit])
        events = E.objects.filter(alert_id__in=ids, created_at__lt=until, event__in=[
            E.Event.INITIAL_EMAIL_SENT, E.Event.INITIAL_EMAIL_FAILED, E.Event.INITIAL_MATCHES_EMPTY,
        ]).order_by("created_at", "pk")
        by_alert = defaultdict(list)
        for event in events:
            by_alert[event.alert_id].append(event)
        outcomes, latest, rejected, review_states = Counter(), Counter(), Counter(), Counter()
        for alert_id in ids:
            rows = by_alert[alert_id]
            if not rows:
                outcomes["no_recorded_initial_outcome"] += 1
                latest["no_recorded_initial_outcome"] += 1
                continue
            first = rows[0]
            outcomes[classify(first)] += 1
            latest[classify(rows[-1])] += 1
            diagnostics = (first.metadata or {}).get("matching") or {}
            diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
            rejections = diagnostics.get("hard_rejections") or {}
            if isinstance(rejections, dict):
                # Fixed vocabulary prevents search text/contact information leaking through a metadata key.
                for key in ("location", "location_rejected", "excluded_location", "salary", "experience", "job_type", "excluded_keyword", "work_authorization"):
                    if type(rejections.get(key)) is int and rejections[key] > 0:
                        rejected[key] += 1
            state = diagnostics.get("reranker")
            if state in ("review_unavailable", "review_partial", "reviewed", "disabled"):
                review_states[state] += 1
        self.stdout.write(json.dumps({"since": since.isoformat(), "until": until.isoformat(),
            "cohort": "New saved Scouts, not people or preview events; first and latest observed outcome per Scout.",
            "scouts": len(ids), "excluded_known_internal_scouts": excluded,
            "capped": query.count() > limit, "first_outcomes": dict(outcomes), "latest_outcomes": dict(latest),
            "scouts_with_repeated_outcome_events": sum(len(v) > 1 for v in by_alert.values()),
            "scouts_with_recorded_hard_rejections": dict(rejected), "recorded_review_states": dict(review_states),
            "caveats": ["Rejection buckets overlap; they are recorded diagnostic evidence, not a proven root cause.",
                        "No model/network calls, restrictions relaxed, messages sent or credit changes.",
                        "Unknown internal/bot/test activity may remain; sample-origin searches are not automatically excluded.",
                        "Email send acceptance does not establish inbox delivery or usefulness.",
                        "Recent Scouts have shorter outcome observation; deleted Scouts are outside this cohort."]}, indent=2))
