from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone as dt_timezone
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from django.db import transaction
from django.utils import timezone
from jobs.models import Job, JobSlugRedirect
from jobs.services.importers.common import (
    _canonical_duplicate_url,
    _job_raw_description_signature,
    _normalize_duplicate_text,
    _normalize_duplicate_title,
    _normalize_duplicate_url,
    UNIQUE_ATS_POSTING_PREFIXES,
)


class Command(BaseCommand):
    help = "Deactivate duplicate imported jobs that point to the same real posting."

    def add_arguments(self, parser):
        parser.add_argument("--report", help="Save retained/deactivated IDs before changing records.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report duplicates without changing rows.",
        )
        parser.add_argument(
            "--strategy",
            choices=["all", "exact-url", "canonical-url", "raw-description"],
            default="all",
            help="Duplicate strategy to use. Defaults to all safe strategies.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum duplicate clusters to process. Useful for a cautious first run.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        strategy = options["strategy"]
        minimum_datetime = datetime.min.replace(tzinfo=dt_timezone.utc)

        jobs = (
            Job.objects.filter(is_active=True)
            .exclude(source=Job.Source.MANUAL)
            .select_related("organization")
            .only(
                "id",
                "source",
                "title",
                "application_url",
                "posted_at",
                "organization_id",
                "organization__name",
                "location",
                "raw_data",
                "description",
                "quality_score",
                "slug", "expires_at", "last_seen_at", "organization__verification_status",
            )
        )

        jobs_by_id = {}
        grouped_keys = defaultdict(list)

        for job in jobs.iterator(chunk_size=1000):
            jobs_by_id[job.id] = job
            source = _normalize_duplicate_text(job.source)
            title = _normalize_duplicate_title(job.title)
            location = _normalize_duplicate_text(job.location)
            if not (source and title and job.organization_id):
                continue

            if strategy in {"all", "exact-url"}:
                exact_url = _normalize_duplicate_url(job.application_url)
                if exact_url:
                    grouped_keys[("exact-url", source, title, job.organization_id, exact_url)].append(job.id)

            if strategy in {"all", "canonical-url"}:
                canonical_url = _canonical_duplicate_url(job.application_url)
                if canonical_url:
                    # Exact title + organization + canonical destination is safe
                    # to merge across aggregators as well as within one source.
                    unique_posting = canonical_url.startswith(UNIQUE_ATS_POSTING_PREFIXES)
                    identity = None if unique_posting else job.organization_id
                    # A strict provider posting ID also survives title corrections
                    # between aggregators; a title alone never identifies a job.
                    grouped_keys[("canonical-url", None if unique_posting else title, identity, canonical_url)].append(job.id)

            if strategy in {"all", "raw-description"}:
                raw_signature = _job_raw_description_signature(job)
                if raw_signature and location:
                    grouped_keys[
                        ("raw-description", source, title, job.organization_id, location, raw_signature)
                    ].append(job.id)

        duplicate_key_groups = [
            (key[0], ids) for key, ids in grouped_keys.items() if len(ids) > 1
        ]

        parent = {}

        def find(job_id):
            parent.setdefault(job_id, job_id)
            if parent[job_id] != job_id:
                parent[job_id] = find(parent[job_id])
            return parent[job_id]

        def union(left, right):
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for _, ids in duplicate_key_groups:
            first_id = ids[0]
            for job_id in ids[1:]:
                union(first_id, job_id)

        components = defaultdict(list)
        for job_id in parent:
            components[find(job_id)].append(jobs_by_id[job_id])

        duplicate_groups = [group for group in components.values() if len(group) > 1]
        duplicate_groups.sort(
            key=lambda group: (
                max(job.posted_at or minimum_datetime for job in group),
                max(job.id for job in group),
            ),
            reverse=True,
        )
        if limit:
            duplicate_groups = duplicate_groups[:limit]

        ids_to_deactivate = []
        examples = []
        report = []
        for group in duplicate_groups:
            group.sort(
                key=lambda job: (job.is_available, job.quality_score, job.posted_at or minimum_datetime, job.id),
                reverse=True,
            )
            keep = group[0]
            duplicates = group[1:]
            ids_to_deactivate.extend(job.id for job in duplicates)
            report.append({"keep": keep.pk, "deactivate": [job.pk for job in duplicates],
                           "duplicate_slugs": [job.slug for job in duplicates], "previous_is_active": True})
            if len(examples) < 8:
                examples.append((keep, duplicates))

        for keep, duplicates in examples:
            duplicate_ids = ", ".join(str(job.id) for job in duplicates[:6])
            if len(duplicates) > 6:
                duplicate_ids += ", ..."
            self.stdout.write(
                f"Keep #{keep.id}: {keep.title} at {keep.organization.name}; "
                f"deactivate {duplicate_ids}"
            )

        strategy_counts = defaultdict(int)
        for name, ids in duplicate_key_groups:
            strategy_counts[name] += len(ids) - 1
        if strategy_counts:
            summary = ", ".join(
                f"{name}: {count}" for name, count in sorted(strategy_counts.items())
            )
            self.stdout.write(f"Duplicate rows by strategy before merging: {summary}")

        if not ids_to_deactivate:
            self.stdout.write(self.style.SUCCESS("No active duplicate jobs found."))
            return

        if options.get("report"):
            Path(options["report"]).write_text(json.dumps(report, indent=2), encoding="utf-8")

        message = (
            f"{'Would deactivate' if dry_run else 'Deactivating'} "
            f"{len(ids_to_deactivate)} duplicate jobs across "
            f"{len(duplicate_groups)} groups."
        )
        self.stdout.write(message)

        if dry_run:
            return

        with transaction.atomic():
            updated = Job.objects.filter(id__in=ids_to_deactivate).update(is_active=False, updated_at=timezone.now())
            # Preserve saved/bookmarked URLs and consolidate the proved posting.
            # Existing editorial redirects are never overwritten.
            aliases = [JobSlugRedirect(old_slug=job.slug, job=keep)
                       for group in duplicate_groups for keep in [group[0]]
                       for job in group[1:] if keep.is_available and job.slug != keep.slug]
            JobSlugRedirect.objects.bulk_create(aliases, ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(f"Deactivated {updated} duplicate jobs."))
