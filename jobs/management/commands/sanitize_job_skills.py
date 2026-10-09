"""Normalize historic skill values to the curated matching taxonomy."""

from django.core.management.base import BaseCommand

from jobs.constants.skills import LEGACY_SKILL_NORMALIZATIONS, SKILLS_BY_SLUG, normalize_skill_slugs
from jobs.models import Job
from jobs.services.job_quality import score_job


class Command(BaseCommand):
    help = "Normalize legacy job skills and preserve unmapped values as audit metadata."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        updates = []
        for job in Job.objects.filter(is_active=True).exclude(skills=[]).select_related("category").iterator(500):
            original = job.skills or []
            cleaned = normalize_skill_slugs(original)
            unmapped = sorted({
                str(value)
                for value in original
                if not isinstance(value, str)
                or LEGACY_SKILL_NORMALIZATIONS.get(
                    value.strip().lower().replace("_", "-").replace(" ", "-"),
                    value.strip().lower().replace("_", "-").replace(" ", "-"),
                ) not in SKILLS_BY_SLUG
            })
            if cleaned == original and not unmapped:
                continue
            raw = dict(job.raw_data or {})
            quality = dict(raw.get("quality_enrichment") or {})
            quality["legacy_skill_cleanup"] = {"method": "taxonomy_normalization_v1", "unmapped_skills": unmapped[:30]}
            raw["quality_enrichment"] = quality
            job.skills, job.raw_data = cleaned, raw
            job.quality_score, job.quality_issues = score_job(job)
            updates.append(job)
        if updates and not options["dry_run"]:
            Job.objects.bulk_update(updates, ["skills", "raw_data", "quality_score", "quality_issues"], batch_size=500)
        self.stdout.write(f"{'Would normalize' if options['dry_run'] else 'Normalized'} {len(updates)} jobs.")
