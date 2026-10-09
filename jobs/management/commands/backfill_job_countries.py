"""Fill the structured country field from the existing location text safely."""
from django.core.management.base import BaseCommand
from django.db.models import Q

from jobs.models import Job
from jobs.services.job_enrichment import infer_country


class Command(BaseCommand):
    help = "Backfill country only when location normalization gives a high-confidence country."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        queryset = Job.objects.filter(
            is_active=True,
        ).filter(Q(country__isnull=True) | Q(country=""))
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        updated = 0
        skipped = 0
        for job in queryset.iterator():
            country = infer_country(job.location or "")
            if country:
                updated += 1
                if not options["dry_run"]:
                    Job.objects.filter(pk=job.pk).update(country=country)
            else:
                skipped += 1

        prefix = "Would update" if options["dry_run"] else "Updated"
        self.stdout.write(f"{prefix} {updated} jobs; skipped {skipped} ambiguous locations")
