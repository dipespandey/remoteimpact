"""Clear recorded homepages that now show a parked or for-sale domain page."""
from django.core.management.base import BaseCommand

from jobs.models import Organization
from jobs.services.org_sites import looks_parked
from jobs.services.safe_fetch import fetch_public_html


class Command(BaseCommand):
    help = "Re-check recorded homepages for parked/for-sale pages (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        cleared = 0
        for org in Organization.objects.exclude(homepage_url="").only("pk", "name", "homepage_url"):
            page = fetch_public_html(org.homepage_url, max_bytes=2_000_000, total_timeout=12)
            if page.status == "ok" and looks_parked(page.text):
                cleared += 1
                self.stdout.write(f"parked: {org.name} {org.homepage_url}")
                if options["apply"]:
                    Organization.objects.filter(pk=org.pk).update(homepage_url="", homepage_source="", sites_checked_at=None)
        self.stdout.write(f"{'cleared' if options['apply'] else 'would clear'} {cleared}")
