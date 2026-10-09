"""Backfill missing structured job fields with deterministic rules."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from jobs.constants import IMPACT_AREAS
from jobs.models import Category, Job
from jobs.services.job_enrichment import enrich_job_payload, is_missing_value
from jobs.services.job_quality import score_job


class Command(BaseCommand):
    help = "Fill missing country, skills, experience, education, and category fields."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--days", type=int, default=None)
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options):
        queryset = Job.objects.filter(is_active=True).filter(
            Q(country__isnull=True)
            | Q(country="")
            | Q(country__iexact="null")
            | Q(skills=[])
            | Q(experience_level__isnull=True)
            | Q(experience_level="")
            | Q(experience_level__iexact="null")
            | Q(education_level__isnull=True)
            | Q(education_level="")
            | Q(education_level__iexact="null")
            | Q(category__isnull=True)
            | Q(salary_min__isnull=True, salary_max__isnull=True)
            | Q(quality_score=0)
        ).select_related("category")
        if options["days"]:
            queryset = queryset.filter(
                posted_at__gte=timezone.now() - timedelta(days=options["days"])
            )
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        categories = self._categories(create=not options["dry_run"])
        pending = []
        counts = {field: 0 for field in (
            "country", "skills", "experience_level", "education_level", "category", "quality"
        )}
        scanned = 0

        for job in queryset.iterator(chunk_size=options["batch_size"]):
            scanned += 1
            enriched = enrich_job_payload({
                "title": job.title,
                "description": job.description,
                "requirements": job.requirements,
                "location": job.location,
                "job_type": job.job_type,
                "country": job.country,
                "skills": job.skills,
                "experience_level": job.experience_level,
                "education_level": job.education_level,
                "category_slug": job.category.slug if job.category_id else "",
                "salary_min": job.salary_min,
                "salary_max": job.salary_max,
                "salary_currency": job.salary_currency,
                "source": job.source,
                "raw_data": job.raw_data,
            })
            changed = False
            for field in ("country", "skills", "experience_level", "education_level"):
                current = getattr(job, field)
                value = enriched.get(field)
                if (not current or is_missing_value(current)) and value:
                    setattr(job, field, value)
                    counts[field] += 1
                    changed = True
                elif isinstance(current, str) and is_missing_value(current):
                    setattr(job, field, None)
                    changed = True
            if job.salary_min is None and job.salary_max is None and enriched.get("salary_min"):
                job.salary_min = enriched["salary_min"]
                job.salary_max = enriched["salary_max"]
                job.salary_currency = enriched["salary_currency"]
                job.raw_data = enriched.get("raw_data", job.raw_data)
                counts["salary"] = counts.get("salary", 0) + 1
                changed = True
            if not job.category_id:
                category = categories.get(enriched.get("category_slug"))
                if category:
                    job.category = category
                    counts["category"] += 1
                    changed = True
            score, issues = score_job(job)
            if job.quality_score != score or job.quality_issues != issues:
                job.quality_score = score
                job.quality_issues = issues
                counts["quality"] += 1
                changed = True
            if changed:
                pending.append(job)
            if len(pending) >= options["batch_size"]:
                self._flush(pending, options["dry_run"])

        self._flush(pending, options["dry_run"])
        action = "Would update" if options["dry_run"] else "Updated"
        details = ", ".join(f"{field}={count}" for field, count in counts.items())
        self.stdout.write(f"{action} {scanned} scanned jobs ({details})")

    @staticmethod
    def _flush(pending, dry_run):
        if pending and not dry_run:
            Job.objects.bulk_update(
                pending,
                ["country", "skills", "experience_level", "education_level", "category",
                 "salary_min", "salary_max", "salary_currency", "raw_data",
                 "quality_score", "quality_issues"],
                batch_size=500,
            )
            for job in pending:
                # Bulk updates bypass signals; refresh lexical/semantic indexes.
                job.embedding_content_hash = ""
                job.save(update_fields=["quality_score"])
        pending.clear()

    @staticmethod
    def _categories(create=True):
        existing = Category.objects.in_bulk(field_name="slug")
        if not create:
            return existing
        result = {}
        for area in IMPACT_AREAS:
            category = existing.get(area["slug"])
            if category is None:
                category, _ = Category.objects.get_or_create(
                    slug=area["slug"],
                    defaults={
                        "name": area["name"],
                        "description": "",
                        "icon": area.get("icon", ""),
                    },
                )
            result[category.slug] = category
        return result
