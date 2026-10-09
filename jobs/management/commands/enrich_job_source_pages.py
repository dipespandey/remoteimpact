"""Use DeepSeek to fill gaps from the original public job page, conservatively."""

from __future__ import annotations

import asyncio

from django.core.management.base import BaseCommand
from django.db.models import Q, Case, When, Value, IntegerField
from django.db.models.functions import Length

from jobs.models import Job
from jobs.services.llm_parser import JobParser
from jobs.services.source_page_enrichment import (
    apply_source_page_enrichment,
    eligible_for_source_enrichment,
    fetch_job_page,
    page_matches_title,
    _record_attempt,
)


class Command(BaseCommand):
    help = "Conservatively enrich incomplete jobs from their source pages with DeepSeek."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--provider", choices=["deepseek"], default="deepseek")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--force", action="store_true", help="Ignore the 30-day per-job retry cooldown.")

    def handle(self, *args, **options):
        candidates = self._candidates(options["limit"], force=options["force"])
        self.stdout.write(f"Selected {len(candidates)} source-page enrichment candidates.")
        if options["dry_run"]:
            for job in candidates:
                self.stdout.write(f"  #{job.id} [{job.source}] {job.title}")
            return

        parser = JobParser(provider=options["provider"])
        enriched = skipped = failed = 0
        for position, job in enumerate(candidates, start=1):
            self.stdout.write(f"[{position}/{len(candidates)}] {job.title[:80]}")
            page = fetch_job_page(job.application_url, title=job.title)
            if page.status != "ok":
                _record_attempt(job, status=page.status)
                fields = ["raw_data"]
                if page.status in {"http_404", "http_410"}:
                    job.is_active = False
                    fields.append("is_active")
                job.save(update_fields=fields)
                skipped += 1
                continue
            if not page_matches_title(job.title, page.text):
                _record_attempt(job, status="title_not_confirmed", page_text=page.text)
                job.save(update_fields=["raw_data"])
                skipped += 1
                continue
            try:
                parsed = asyncio.run(
                    parser.parse_single(job.title, job.organization.name, page.text)
                )
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f"  DeepSeek failed: {exc}"))
                _record_attempt(job, status="llm_failed", page_text=page.text)
                job.save(update_fields=["raw_data"])
                failed += 1
                continue
            if not parsed:
                _record_attempt(job, status="no_parse", page_text=page.text)
                job.save(update_fields=["raw_data"])
                failed += 1
                continue
            fields = apply_source_page_enrichment(job, parsed, page.text)
            job.save(update_fields=fields)
            if len(fields) > 1:
                enriched += 1
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f"Source-page enrichment complete: enriched={enriched}, skipped={skipped}, failed={failed}."
        ))

    @staticmethod
    def _candidates(limit: int, *, force: bool) -> list[Job]:
        needs_enrichment = (
            Q(salary_min__isnull=True, salary_max__isnull=True)
            | Q(experience_level__isnull=True)
            | Q(experience_level="")
            | Q(education_level__isnull=True)
            | Q(education_level="")
            | Q(category__isnull=True)
            | Q(category__slug="other")
            | Q(description_length__lt=200)
        )
        pool = (
            Job.objects.filter(is_active=True)
            .annotate(description_length=Length("description"))
            .exclude(source__in=[Job.Source.MANUAL, Job.Source.IDEALIST])
            .exclude(application_url="")
            .filter(needs_enrichment)
            .select_related("organization", "category")
            .annotate(enrichment_priority=Case(
                When(description_length__lt=200, then=Value(0)),
                When(Q(category__isnull=True) | Q(category__slug="other"), then=Value(1)),
                default=Value(2), output_field=IntegerField(),
            ))
            .order_by("enrichment_priority", "-posted_at", "-id")
        )
        candidates = []
        for job in pool.iterator(chunk_size=200):
            if eligible_for_source_enrichment(job, force=force):
                candidates.append(job)
            if len(candidates) >= limit:
                break
        return candidates
