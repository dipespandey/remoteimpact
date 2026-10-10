"""Show what close matches recent empty Scouts would have received. Sends nothing."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import ConsumerFunnelEvent, JobAlert
from jobs.services.job_scout_service import JobScoutService
from jobs.services.job_service import JobService
from jobs.services.scout_close_matches import close_matches


class Command(BaseCommand):
    help = "Dry run: close matches for Scouts whose first run was empty."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14)
        parser.add_argument("--limit", type=int, default=40)

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=options["days"])
        alert_ids = (ConsumerFunnelEvent.objects.filter(event=ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY,
                                                        created_at__gte=since, alert__isnull=False)
                     .values_list("alert_id", flat=True).distinct())
        alerts = JobAlert.objects.filter(pk__in=list(alert_ids)[: options["limit"]])
        helped = total = 0
        for alert in alerts:
            preferences = JobScoutService.preferences_for_alert(alert)
            if not preferences.get("intent_text"):
                continue
            total += 1
            qs = JobService.get_available_jobs().filter(posted_at__gte=timezone.now() - timedelta(days=30)).select_related("organization")
            terms = JobScoutService._search_terms(preferences)
            picks = close_matches(qs, preferences, terms, {}, force=True)
            helped += bool(picks)
            self.stdout.write(f"\n#{alert.pk} {preferences['intent_text'][:220]!r}")
            for job in picks:
                self.stdout.write(f"   - {job.title} @ {job.organization.name} ({job.location}) :: {job.scout_reasons[0]}")
            if not picks:
                self.stdout.write("   (none picked)")
        self.stdout.write(f"\n{helped}/{total} empty Scouts would get at least one close match")
