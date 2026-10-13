"""Jobs API and MCP usage: calls by surface, endpoint/tool, tier and client, plus keys."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from jobs.models import ApiKey, ApiUsage


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, **options):
        since = timezone.localdate() - timedelta(days=options["days"] - 1)
        rows = ApiUsage.objects.filter(date__gte=since)
        self.stdout.write(f"Last {options['days']} days: {rows.aggregate(n=Sum('count'))['n'] or 0} calls; "
                          f"{ApiKey.objects.count()} keys ({ApiKey.objects.filter(is_active=True).count()} active)")
        for label, field in (("surface", "surface"), ("endpoint/tool", "endpoint"), ("tier", "tier"), ("client", "client"), ("status", "status")):
            top = rows.values(field).annotate(n=Sum("count")).order_by("-n")[:8]
            self.stdout.write(f"  {label}: " + ", ".join(f"{r[field] or '-'} {r['n']}" for r in top))
        by_day = rows.values("date").annotate(n=Sum("count")).order_by("date")
        self.stdout.write("  per day: " + ", ".join(f"{r['date']:%m-%d} {r['n']}" for r in by_day))
