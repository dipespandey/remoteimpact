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
        viewed = totals.get(EmployerFunnelEvent.Event.POST_JOB_VIEWED, 0)
        paid = totals.get(EmployerFunnelEvent.Event.PAYMENT_COMPLETED, 0)
        self.stdout.write(f"Employer funnel, last {options['days']} days")
        for event in EmployerFunnelEvent.Event:
            row = next((item for item in rows if item["event"] == event.value), None)
            total = row["total"] if row else 0
            users = row["users"] if row else 0
            self.stdout.write(f"  {event.label}: {total} events, {users} users")
        if viewed:
            self.stdout.write(f"  View-to-paid rate: {paid / viewed:.1%}")
        else:
            self.stdout.write("  View-to-paid rate: n/a")
