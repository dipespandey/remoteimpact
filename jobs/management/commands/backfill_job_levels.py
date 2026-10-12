"""Fill missing experience/education levels from grounded model labels, and lower an education level the
posting's own quote shows is only preferred. Every value needs a verbatim quote found in the posting.

Rules: experience is only ever filled when empty (an existing label, e.g. executive for a Director, is kept);
education is filled when empty and lowered when the verified quote shows a lower minimum.
Dry run unless --apply.
"""
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from jobs.management.commands.label_job_fields import label
from jobs.models import Job
from jobs.services.ai import AIClient
from jobs.services.job_service import JobService

EDU_RANK = {"high_school": 0, "associate": 1, "bachelor": 2, "master": 3, "phd": 4}


class Command(BaseCommand):
    help = "Backfill experience/education levels with grounded labels (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--workers", type=int, default=6)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        live = JobService.get_available_jobs()
        jobs = list(live.filter(Q(experience_level__isnull=True) | Q(experience_level="") | Q(education_level__isnull=True)
                                | Q(education_level="") | Q(education_level__in=["master", "phd"])).order_by("pk"))
        if options["limit"]:
            jobs = jobs[: options["limit"]]
        client = AIClient()
        with ThreadPoolExecutor(max_workers=options["workers"]) as pool:
            rows = [r for r in pool.map(lambda j: label(client, j), jobs) if r]
        by_pk = {j.pk: j for j in jobs}
        counts = {"labelled": len(rows), "experience_filled": 0, "education_filled": 0, "education_lowered": 0}
        for row in rows:
            job = by_pk[row["job"]]
            update, evidence = {}, {}
            if not job.experience_level and row["experience_level"] != "unstated":
                update["experience_level"] = row["experience_level"]
                evidence["experience"] = {"value": row["experience_level"], "quote": row["experience_quote"]}
                counts["experience_filled"] += 1
            new_edu = row["education"]
            if new_edu != "unstated":
                if not job.education_level:
                    update["education_level"] = new_edu
                    counts["education_filled"] += 1
                elif EDU_RANK.get(new_edu, 9) < EDU_RANK.get(job.education_level, 0):
                    update["education_level"] = new_edu
                    counts["education_lowered"] += 1
                if "education_level" in update:
                    evidence["education"] = {"previous": job.education_level, "value": new_edu, "quote": row["education_quote"]}
            if update and options["apply"]:
                raw = dict(job.raw_data or {})
                raw["level_evidence"] = {**evidence, "method": "llm_grounded_v1", "at": timezone.now().isoformat()}
                Job.objects.filter(pk=job.pk).update(raw_data=raw, **update)
        self.stdout.write(str(counts) + (" (applied)" if options["apply"] else " (dry run)"))
