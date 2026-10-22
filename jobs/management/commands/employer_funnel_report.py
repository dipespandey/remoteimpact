from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from jobs.models import EmployerFunnelEvent


class Command(BaseCommand):
    help = "Report employer posting funnel events for a recent period"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=options["days"])
        rows = list(
            EmployerFunnelEvent.objects.filter(created_at__gte=since)
            .values("event")
            .annotate(total=Count("id"), users=Count("user_id", distinct=True))
            .order_by("event")
        )
        totals = {row["event"]: row["total"] for row in rows}
        self.stdout.write(f"Employer funnel, last {options['days']} days")
        for event in EmployerFunnelEvent.Event:
            row = next((item for item in rows if item["event"] == event.value), None)
            total = row["total"] if row else 0
            users = row["users"] if row else 0
            self.stdout.write(f"  {event.label}: {total} events, {users} users")
        self.stdout.write("Page requests are unverified, not unique people or employer interest. "
                          "Event totals are not a cohort conversion rate; promotion requests are separate.")
