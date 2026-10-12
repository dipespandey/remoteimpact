"""How people respond to what we rank: impressions, click-through by position and surface, applies, saves.

  ranking_report [--days 14] [--purge]     (--purge deletes events older than the 120-day retention)
"""
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import RankingEvent
from jobs.services import ranking_log


class Command(BaseCommand):
    help = "Summarize ranking events (impressions, clicks by position, applies, saves)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14)
        parser.add_argument("--purge", action="store_true")

    def handle(self, *args, **options):
        if options["purge"]:
            self.stdout.write(f"Purged {ranking_log.purge_old()} old events.")
        since = timezone.now() - timedelta(days=options["days"])
        events = RankingEvent.objects.filter(created_at__gte=since)
        impressions = events.filter(kind="impression")
        clicks = events.filter(kind="click")
        self.stdout.write(f"Last {options['days']} days: {impressions.count()} result lists shown, "
                          f"{clicks.count()} clicks, {events.filter(kind='apply').count()} applies, "
                          f"{events.filter(kind='save').count()} saves.")
        by_surface = Counter(impressions.values_list("surface", flat=True))
        clicks_by_surface = Counter(clicks.values_list("surface", flat=True))
        for surface, shown in by_surface.most_common():
            self.stdout.write(f"  {surface or 'unknown':10} {shown:6} lists, {clicks_by_surface[surface]:5} clicks "
                              f"({clicks_by_surface[surface] / shown:.1%} per list)")
        shown_at = Counter()
        for ids in impressions.values_list("job_ids", flat=True):
            for position in range(1, min(len(ids), 10) + 1):
                shown_at[position] += 1
        clicked_at = Counter(clicks.exclude(position=None).values_list("position", flat=True))
        self.stdout.write("  Click-through by position: " + ", ".join(
            f"{p}: {clicked_at[p] / shown_at[p]:.1%}" for p in range(1, 11) if shown_at[p]))
        # Clicks that led to an apply by the same visitor on the same job.
        clicked = set(clicks.values_list("visitor", "job_id"))
        applied = set(events.filter(kind="apply").values_list("visitor", "job_id"))
        if clicked:
            self.stdout.write(f"  Clicks followed by an apply click: {len(clicked & applied) / len(clicked):.1%}")
