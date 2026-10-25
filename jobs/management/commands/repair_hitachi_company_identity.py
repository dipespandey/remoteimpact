"""Move explicitly identified Hitachi group roles out of the Energy profile.

Dry run by default. Only Climatebase roles previously assigned to Hitachi Energy
are eligible. The importer uses the same rule for subsequent refreshes.
"""

from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from jobs.models import Job
from jobs.services.importers.climatebase import employer_identity
from jobs.services.importers.common import _get_or_create_org


class Command(BaseCommand):
    help = "Correct explicitly named Hitachi companies on Climatebase roles (dry run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the reviewed assignments.")

    def handle(self, *args, **options):
        candidates = Job.objects.filter(source=Job.Source.CLIMATEBASE, organization__slug="hitachi-energy")
        assignments = []
        counts = Counter()
        for job in candidates.iterator():
            name = employer_identity("Hitachi Energy", job.description)
            if name.casefold() == "hitachi energy":
                continue
            assignments.append((job.pk, name))
            counts[name] += 1
        for name, count in sorted(counts.items()):
            self.stdout.write(f"{name}: {count}")
        self.stdout.write(f"Total to reassign: {len(assignments)}")
        if not options["apply"]:
            self.stdout.write("Dry run; pass --apply after reviewing the counts.")
            return
        with transaction.atomic():
            organizations = {name: _get_or_create_org(name) for name in counts}
            now = timezone.now()
            for pk, name in assignments:
                Job.objects.filter(pk=pk, source=Job.Source.CLIMATEBASE,
                                   organization__slug="hitachi-energy").update(
                    organization=organizations[name], updated_at=now,
                    embedding=None, embedding_content_hash="",
                )
        self.stdout.write(self.style.SUCCESS(f"Reassigned {len(assignments)} roles."))
