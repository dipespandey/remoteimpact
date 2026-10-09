from django.core.management import call_command
from django.core.management.base import BaseCommand
from jobs.models import Category, Job
from jobs.services.job_enrichment import is_missing_value
from jobs.services.job_quality import better_category_slug, normalize_salary, score_job


class Command(BaseCommand):
    help = "Run the idempotent daily job data-quality pipeline."

    def add_arguments(self, parser):
        parser.add_argument("--include-ai", action="store_true", help="Explicitly opt in to paid DeepSeek enrichment (up to25skills/20pages).")

    def handle(self, *args, **options):
        call_command("deactivate_expired_jobs", stdout=self.stdout)
        call_command("deactivate_duplicate_jobs", stdout=self.stdout)
        call_command("deactivate_non_opening_jobs", stdout=self.stdout)
        # ~500/day revisits every live listing roughly every two weeks.
        call_command("check_stale_jobs", "--limit", "500", "--delay", "0.2", stdout=self.stdout)
        call_command("backfill_job_quality", "--days", "30", stdout=self.stdout)
        call_command("repair_legacy_skill_tags", stdout=self.stdout)
        call_command("sanitize_job_skills", stdout=self.stdout)
        if options["include_ai"]:
            call_command(
                "backfill_skills", "--limit", "25", "--batch-size", "25", "--provider", "deepseek",
                stdout=self.stdout,
            )
            # The importers already preserve source data. This small, bounded pass
            # consults the original public job page only for fields that remain
            # missing, and accepts salary/education/experience only with evidence.
            try:
                call_command(
                    "enrich_job_source_pages", "--limit", "20", "--provider", "deepseek",
                    stdout=self.stdout,
                )
            except Exception as exc:
                # The core deterministic cleanup should never be blocked by a
                # temporary upstream page or LLM outage.
                self.stderr.write(self.style.WARNING(
                    f"Source-page enrichment skipped: {exc}"
                ))
        else:
            self.stdout.write("Paid AI enrichment skipped; use --include-ai only with an approved recurring budget.")

        categories = Category.objects.in_bulk(field_name="slug")
        updated = salary_updates = category_updates = 0
        pending = []
        for job in Job.objects.filter(is_active=True).select_related("category").iterator(chunk_size=500):
            fields = normalize_salary(job)
            if fields:
                salary_updates += 1
            for field in ("country", "experience_level", "education_level"):
                if is_missing_value(getattr(job, field)) and getattr(job, field) is not None:
                    setattr(job, field, None)
                    fields.append(field)
            slug = better_category_slug(job)
            if slug and slug in categories:
                job.category = categories[slug]
                fields.append("category")
                category_updates += 1
            score, issues = score_job(job)
            if job.quality_score != score:
                job.quality_score = score
                fields.append("quality_score")
            if job.quality_issues != issues:
                job.quality_issues = issues
                fields.append("quality_issues")
            if fields:
                pending.append(job)
                updated += 1
            if len(pending) >= 500:
                self._flush(pending)
        self._flush(pending)
        call_command("embed_jobs", "--stale", "--limit", "500", stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(
            f"Quality pipeline updated {updated} jobs "
            f"(salary={salary_updates}, category={category_updates})."
        ))

    @staticmethod
    def _flush(jobs):
        if not jobs:
            return
        Job.objects.bulk_update(
            jobs,
            [
                "salary_min", "salary_max", "salary_currency", "raw_data",
                "category", "quality_score", "quality_issues", "country",
                "experience_level", "education_level",
            ],
            batch_size=500,
        )
        for job in jobs:
            job.embedding_content_hash = ""
            job.save(update_fields=["quality_score"])
        jobs.clear()
