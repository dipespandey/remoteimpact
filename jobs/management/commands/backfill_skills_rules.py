from django.core.management.base import BaseCommand
from django.db.models import Q

from jobs.models import Job
from jobs.services.matching_service import MatchingService


class Command(BaseCommand):
    help = "Backfill only high-confidence skill labels found literally in job text."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        jobs = Job.objects.filter(is_active=True).filter(Q(skills__isnull=True) | Q(skills=[]))
        if options["limit"]:
            jobs = jobs[: options["limit"]]

        updates = []
        for job in jobs.iterator():
            text = f"{job.title}\n{job.description}\n{job.requirements}"
            skills = sorted(MatchingService._extract_skills_from_text(text))[:15]
            if skills:
                job.skills = skills
                updates.append(job)

        if not options["dry_run"] and updates:
            Job.objects.bulk_update(updates, ["skills"], batch_size=500)
        action = "Would update" if options["dry_run"] else "Updated"
        self.stdout.write(f"{action} {len(updates)} jobs with literal skill matches")
