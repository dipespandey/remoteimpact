"""Repair a false-positive legacy skill tag without altering other job data."""

import re

from django.core.management.base import BaseCommand

from jobs.models import Job
from jobs.services.job_quality import score_job


class Command(BaseCommand):
    help = "Remove spurious legacy R skill tags while preserving explicit R-language roles."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        jobs = Job.objects.filter(is_active=True).exclude(skills=[]).select_related("category")

        updates = []
        removed = retained = 0
        scanned = 0
        for job in jobs.iterator(chunk_size=500):
            if "r" not in (job.skills or []):
                continue
            scanned += 1
            if options["limit"] and scanned > options["limit"]:
                break
            text = " ".join((job.title or "", job.requirements or "", job.description or ""))
            explicit_r = bool(re.search(r"(?<!\w)R(?!\w)", text))
            skills = set(job.skills or [])
            skills.discard("r")
            if explicit_r:
                skills.add("r")
                retained += 1
            else:
                removed += 1

            cleaned = sorted(skills)
            if cleaned == (job.skills or []):
                continue
            job.skills = cleaned
            job.quality_score, job.quality_issues = score_job(job)
            updates.append(job)

        if updates and not options["dry_run"]:
            Job.objects.bulk_update(
                updates,
                ["skills", "quality_score", "quality_issues"],
                batch_size=500,
            )
        action = "Would repair" if options["dry_run"] else "Repaired"
        self.stdout.write(
            f"{action} {len(updates)} jobs (removed r={removed}, retained explicit R={retained})."
        )
