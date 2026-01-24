"""
Management command to backfill structured fields from raw_data.

This is a NO-COST operation - pure Python parsing, no AI involved.

Usage:
    python manage.py backfill_from_raw_data --source climatebase --dry-run
    python manage.py backfill_from_raw_data --source all --batch-size 500
    python manage.py backfill_from_raw_data --source 80000hours --only-missing
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from jobs.models import Job
from jobs.services.raw_data_extractor import RawDataExtractor


class Command(BaseCommand):
    help = "Backfill experience_level, education_level, country from raw_data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=str,
            default="all",
            help="Source to backfill: climatebase, 80000hours, idealist, all",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of jobs to process per batch",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be updated without making changes",
        )
        parser.add_argument(
            "--only-missing",
            action="store_true",
            help="Only fill fields that are currently NULL",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of jobs to process",
        )

    def handle(self, *args, **options):
        source = options["source"]
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]
        only_missing = options["only_missing"]
        limit = options["limit"]

        self.stdout.write(
            self.style.HTTP_INFO(
                f"Starting backfill from raw_data (source={source}, dry_run={dry_run})"
            )
        )

        # Build queryset
        queryset = Job.objects.filter(is_active=True)

        if source != "all":
            queryset = queryset.filter(source=source)
        else:
            # Process sources that have structured raw_data
            queryset = queryset.filter(
                source__in=["climatebase", "80000hours", "idealist", "reliefweb"]
            )

        queryset = queryset.exclude(raw_data__isnull=True).exclude(raw_data={})

        if limit:
            queryset = queryset[:limit]

        total = queryset.count()
        self.stdout.write(f"Found {total} jobs to process")

        stats = {
            "processed": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "fields_updated": {
                "experience_level": 0,
                "education_level": 0,
                "country": 0,
                "salary_min": 0,
                "salary_max": 0,
                "job_type": 0,
                "location": 0,
                "posted_at": 0,
                "expires_at": 0,
            },
        }

        # Process in batches
        offset = 0
        while offset < total:
            batch = list(queryset[offset : offset + batch_size])
            if not batch:
                break

            self.stdout.write(f"Processing batch {offset}-{offset + len(batch)}...")

            updates = []
            for job in batch:
                try:
                    extracted = RawDataExtractor.extract(job.source, job.raw_data)
                    if not extracted:
                        stats["skipped"] += 1
                        continue

                    update_fields = []

                    # Experience level
                    if extracted.get("experience_level"):
                        if not only_missing or not job.experience_level:
                            if job.experience_level != extracted["experience_level"]:
                                job.experience_level = extracted["experience_level"]
                                update_fields.append("experience_level")
                                stats["fields_updated"]["experience_level"] += 1

                    # Education level
                    if extracted.get("education_level"):
                        if not only_missing or not job.education_level:
                            if job.education_level != extracted["education_level"]:
                                job.education_level = extracted["education_level"]
                                update_fields.append("education_level")
                                stats["fields_updated"]["education_level"] += 1

                    # Country
                    if extracted.get("country"):
                        if not only_missing or not job.country:
                            if job.country != extracted["country"]:
                                job.country = extracted["country"]
                                update_fields.append("country")
                                stats["fields_updated"]["country"] += 1

                    # Salary min
                    if extracted.get("salary_min") is not None:
                        if not only_missing or job.salary_min is None:
                            if job.salary_min != extracted["salary_min"]:
                                job.salary_min = extracted["salary_min"]
                                update_fields.append("salary_min")
                                stats["fields_updated"]["salary_min"] += 1

                    # Salary max
                    if extracted.get("salary_max") is not None:
                        if not only_missing or job.salary_max is None:
                            if job.salary_max != extracted["salary_max"]:
                                job.salary_max = extracted["salary_max"]
                                update_fields.append("salary_max")
                                stats["fields_updated"]["salary_max"] += 1

                    # Salary currency
                    if extracted.get("salary_currency"):
                        if job.salary_currency != extracted["salary_currency"]:
                            job.salary_currency = extracted["salary_currency"]
                            update_fields.append("salary_currency")

                    # Job type (only if different from current)
                    if extracted.get("job_type"):
                        if not only_missing or job.job_type == "full-time":  # default
                            if job.job_type != extracted["job_type"]:
                                job.job_type = extracted["job_type"]
                                update_fields.append("job_type")
                                stats["fields_updated"]["job_type"] += 1

                    # Location (only if more specific than current)
                    if extracted.get("location"):
                        current_loc = job.location or ""
                        new_loc = extracted["location"]
                        # Only update if new location is more specific
                        if (
                            current_loc.lower() == "remote"
                            and new_loc.lower() != "remote"
                        ):
                            job.location = new_loc
                            update_fields.append("location")
                            stats["fields_updated"]["location"] += 1

                    # Posted at (original posting date)
                    if extracted.get("posted_at"):
                        # Only update if we don't have it or it's clearly wrong
                        # (e.g., posted_at is very recent but job is from older source)
                        if not only_missing or job.posted_at is None:
                            new_posted = extracted["posted_at"]
                            if job.posted_at != new_posted:
                                job.posted_at = new_posted
                                update_fields.append("posted_at")
                                stats["fields_updated"]["posted_at"] += 1

                    # Expires at (deadline)
                    if extracted.get("expires_at"):
                        if not only_missing or job.expires_at is None:
                            new_expires = extracted["expires_at"]
                            if job.expires_at != new_expires:
                                job.expires_at = new_expires
                                update_fields.append("expires_at")
                                stats["fields_updated"]["expires_at"] += 1

                    if update_fields:
                        updates.append((job, update_fields))
                        stats["updated"] += 1
                    else:
                        stats["skipped"] += 1

                    stats["processed"] += 1

                except Exception as e:
                    self.stderr.write(f"Error processing job {job.id}: {e}")
                    stats["errors"] += 1

            # Save updates
            if updates and not dry_run:
                with transaction.atomic():
                    for job, fields in updates:
                        job.save(update_fields=fields)

            offset += batch_size

        # Print summary
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(self.style.SUCCESS("Backfill complete!"))
        self.stdout.write(f"Total processed: {stats['processed']}")
        self.stdout.write(f"Jobs updated: {stats['updated']}")
        self.stdout.write(f"Jobs skipped: {stats['skipped']}")
        self.stdout.write(f"Errors: {stats['errors']}")
        self.stdout.write("\nFields updated:")
        for field, count in stats["fields_updated"].items():
            self.stdout.write(f"  {field}: {count}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("\n[DRY RUN] No changes were saved to database")
            )
