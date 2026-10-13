"""Keep featured roles current for organizations on an active plan (run hourly)."""
from django.core.management.base import BaseCommand

from jobs.services import org_plans


class Command(BaseCommand):
    help = "Feature the newest live roles, up to each plan's cap, for active organization plans."

    def handle(self, *args, **options):
        self.stdout.write(f"Featured {org_plans.sync_featuring()} role(s).")
