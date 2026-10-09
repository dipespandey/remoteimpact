from django.core.management.base import BaseCommand
from django.db.models import F

from jobs.models import Job


class Command(BaseCommand):
    help = "Remove mathematically invalid salary ranges while preserving their raw values."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        jobs = Job.objects.filter(
            is_active=True,
            salary_min__isnull=False,
            salary_max__isnull=False,
            salary_min__gt=F("salary_max"),
        )
        count = jobs.count()
        if options["dry_run"]:
            self.stdout.write(f"Would quarantine {count} invalid salary ranges")
            return

        for job in jobs.iterator():
            raw_data = dict(job.raw_data or {})
            raw_data["invalid_salary_quarantined"] = {
                "salary_min": str(job.salary_min),
                "salary_max": str(job.salary_max),
            }
            job.raw_data = raw_data
            job.salary_min = None
            job.salary_max = None
            job.save(update_fields=["raw_data", "salary_min", "salary_max"])
        self.stdout.write(self.style.SUCCESS(f"Quarantined {count} invalid salary ranges"))
