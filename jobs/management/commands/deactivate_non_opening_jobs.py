"""Remove imported talent pools and generic application forms from public inventory."""

from django.core.management.base import BaseCommand
from django.db.models import F

from jobs.models import Job
from jobs.services.importers.common import _is_generic_listing_title
from jobs.services.scout_verification import source_non_opening


class Command(BaseCommand):
    help = "Deactivate imported records that are not concrete job openings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report records without changing their active status.",
        )

    def handle(self, *args, **options):
        candidates = (
            Job.objects.filter(is_active=True)
            .exclude(source=Job.Source.MANUAL)
            .annotate(source_workloads=F('raw_data__tags_workload'))
            .only("id", "title", "source")
        )
        ids = [
            job.id
            for job in candidates.iterator(chunk_size=500)
            if _is_generic_listing_title(job.title) or source_non_opening(
                job.source, {'tags_workload': job.source_workloads})
        ]

        if not ids:
            self.stdout.write(self.style.SUCCESS("No non-opening records found."))
            return

        self.stdout.write(f"Found {len(ids)} non-opening imported records.")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("[DRY RUN] No changes were made."))
            return

        updated = Job.objects.filter(id__in=ids).update(is_active=False)
        self.stdout.write(self.style.SUCCESS(f"Deactivated {updated} non-opening records."))
