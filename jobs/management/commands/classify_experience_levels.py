"""
Management command to classify experience levels for jobs using rule-based detection.

Usage:
    python manage.py classify_experience_levels --dry-run
    python manage.py classify_experience_levels --only-missing
    python manage.py classify_experience_levels --limit 100
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from jobs.models import Job
from jobs.services.experience_classifier import ExperienceClassifier


class Command(BaseCommand):
    help = "Classify experience levels for jobs based on title/description keywords"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be updated without making changes",
        )
        parser.add_argument(
            "--only-missing",
            action="store_true",
            help="Only classify jobs that have no experience_level set",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of jobs to process",
        )
        parser.add_argument(
            "--show-samples",
            action="store_true",
            help="Show sample classifications for each level",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        only_missing = options["only_missing"]
        limit = options["limit"]
        show_samples = options["show_samples"]

        self.stdout.write(
            self.style.HTTP_INFO(
                f"Starting experience level classification (dry_run={dry_run})"
            )
        )

        # Build queryset
        queryset = Job.objects.filter(is_active=True)

        if only_missing:
            queryset = queryset.filter(
                Q(experience_level__isnull=True) | Q(experience_level="")
            )

        if limit:
            queryset = queryset[:limit]

        total = queryset.count()
        self.stdout.write(f"Processing {total} jobs...")

        stats = {
            "processed": 0,
            "classified": 0,
            "already_set": 0,
            "no_match": 0,
            "by_level": {
                "internship": 0,
                "entry": 0,
                "mid": 0,
                "senior": 0,
                "executive": 0,
            },
        }

        samples = {
            "internship": [],
            "entry": [],
            "mid": [],
            "senior": [],
            "executive": [],
        }

        updates = []

        for job in queryset.iterator():
            stats["processed"] += 1

            level, reason = ExperienceClassifier.get_best_classification(
                title=job.title,
                description=job.description or "",
                job_type=job.job_type or "",
                existing_level=job.experience_level if not only_missing else None,
            )

            if level:
                if job.experience_level != level:
                    old_level = job.experience_level
                    job.experience_level = level
                    updates.append(job)
                    stats["classified"] += 1
                    stats["by_level"][level] += 1

                    # Collect samples
                    if show_samples and len(samples[level]) < 5:
                        samples[level].append({
                            "title": job.title,
                            "old": old_level,
                            "new": level,
                            "reason": reason,
                        })
                else:
                    stats["already_set"] += 1
            else:
                stats["no_match"] += 1

            # Progress update
            if stats["processed"] % 1000 == 0:
                self.stdout.write(f"  Processed {stats['processed']}/{total}...")

        # Save updates
        if updates and not dry_run:
            self.stdout.write(f"Saving {len(updates)} updates...")
            with transaction.atomic():
                Job.objects.bulk_update(updates, ["experience_level"], batch_size=500)

        # Print summary
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(self.style.SUCCESS("Classification complete!"))
        self.stdout.write(f"Total processed: {stats['processed']}")
        self.stdout.write(f"Newly classified: {stats['classified']}")
        self.stdout.write(f"Already had correct level: {stats['already_set']}")
        self.stdout.write(f"No match found: {stats['no_match']}")
        self.stdout.write("\nBy level:")
        for level, count in stats["by_level"].items():
            self.stdout.write(f"  {level}: {count}")

        if show_samples:
            self.stdout.write("\n" + "=" * 50)
            self.stdout.write("SAMPLES BY LEVEL:")
            for level, jobs in samples.items():
                if jobs:
                    self.stdout.write(f"\n{level.upper()}:")
                    for job in jobs:
                        self.stdout.write(f"  - {job['title']}")
                        self.stdout.write(f"    Old: {job['old']} -> New: {job['new']}")
                        self.stdout.write(f"    Reason: {job['reason']}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("\n[DRY RUN] No changes were saved to database")
            )
