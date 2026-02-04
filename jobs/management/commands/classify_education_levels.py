"""
Management command to classify education levels for existing jobs.

Usage:
    python manage.py classify_education_levels              # Classify all jobs missing education_level
    python manage.py classify_education_levels --limit 100  # Limit to 100 jobs
    python manage.py classify_education_levels --dry-run    # Preview without saving
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from jobs.models import Job
from jobs.services.education_classifier import EducationClassifier


class Command(BaseCommand):
    help = "Classify education levels for jobs using rule-based classifier"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of jobs to process",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview classifications without saving",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-classify even if education_level is already set",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        dry_run = options["dry_run"]
        force = options["force"]

        # Build query
        queryset = Job.objects.filter(is_active=True)
        
        if not force:
            # Only jobs without education_level
            queryset = queryset.filter(
                Q(education_level__isnull=True) | Q(education_level="")
            )
        
        # Must have description to classify
        queryset = queryset.exclude(description="").exclude(description__isnull=True)

        if limit:
            queryset = queryset[:limit]

        jobs = list(queryset)
        self.stdout.write(f"Found {len(jobs)} jobs to classify")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be saved"))

        stats = {
            'high_school': 0,
            'associate': 0,
            'bachelor': 0,
            'master': 0,
            'phd': 0,
            'unchanged': 0,
        }

        for i, job in enumerate(jobs):
            level, reason = EducationClassifier.get_best_classification(
                description=job.description or "",
                requirements=job.requirements or "",
                existing_level=job.education_level if not force else None,
            )

            if level and level != job.education_level:
                stats[level] = stats.get(level, 0) + 1
                
                if not dry_run:
                    job.education_level = level
                    job.save(update_fields=['education_level'])
                
                if options["verbosity"] >= 2:
                    self.stdout.write(f"  [{level}] {job.title[:50]} - {reason}")
            else:
                stats['unchanged'] += 1

            # Progress
            if (i + 1) % 500 == 0:
                self.stdout.write(f"  Processed {i + 1}/{len(jobs)} jobs...")

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Classification complete:"))
        for level, count in sorted(stats.items()):
            if count > 0:
                self.stdout.write(f"  {level}: {count}")
