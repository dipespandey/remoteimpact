"""
Management command to fix job salaries by extracting from descriptions.

Finds jobs where:
1. No salary is set but description contains salary info
2. Salary is set but description has different (likely more accurate) range
"""
import logging
from django.core.management.base import BaseCommand
from django.db.models import Q
from jobs.models import Job
from jobs.services.crawlers.base import extract_salary_from_text

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Fix job salaries by extracting from descriptions"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without saving",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of jobs to process",
        )
        parser.add_argument(
            "--job-id",
            type=int,
            default=None,
            help="Fix a specific job by ID",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Update salary even if already set (use extracted value if it's a wider range)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        job_id = options["job_id"]
        force = options["force"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be saved\n"))

        # Build query
        queryset = Job.objects.filter(is_active=True)
        
        if job_id:
            queryset = queryset.filter(id=job_id)
        elif not force:
            # Only jobs missing salary data
            queryset = queryset.filter(
                Q(salary_min__isnull=True) | Q(salary_max__isnull=True)
            )

        if limit:
            queryset = queryset[:limit]

        jobs = list(queryset)
        self.stdout.write(f"Processing {len(jobs)} jobs...\n")

        stats = {
            "processed": 0,
            "updated": 0,
            "no_salary_found": 0,
            "already_correct": 0,
            "errors": 0,
        }

        for job in jobs:
            stats["processed"] += 1
            
            # Get description text
            description = job.description or ""
            
            # Also check raw_data for original content
            if job.raw_data and isinstance(job.raw_data, dict):
                api_response = job.raw_data.get("api_response", {})
                if api_response.get("content"):
                    description += "\n" + api_response.get("content", "")
            
            if not description:
                continue

            try:
                extracted = extract_salary_from_text(description)
                
                if not extracted:
                    stats["no_salary_found"] += 1
                    continue

                new_min = extracted.get("salary_min")
                new_max = extracted.get("salary_max")
                new_currency = extracted.get("salary_currency", "USD")

                # Check if update is needed
                current_min = float(job.salary_min) if job.salary_min else None
                current_max = float(job.salary_max) if job.salary_max else None

                needs_update = False
                reason = ""

                if current_min is None and current_max is None:
                    # No salary set, use extracted
                    needs_update = True
                    reason = "no salary was set"
                elif force and new_min and new_max:
                    # Force mode - check if extracted range is different/wider
                    if new_min != current_min or new_max != current_max:
                        # Prefer wider ranges (more likely to be accurate)
                        if new_max and current_max and new_max > current_max:
                            needs_update = True
                            reason = f"extracted range is wider (${new_min:,.0f}-${new_max:,.0f} vs ${current_min:,.0f}-${current_max:,.0f})"
                        elif current_min == current_max and new_min != new_max:
                            # Currently has single value, extracted has range
                            needs_update = True
                            reason = f"extracted has range vs single value"

                if needs_update and new_min and new_max:
                    self.stdout.write(
                        f"\n[{job.id}] {job.title[:50]}..."
                        f"\n  Organization: {job.organization.name if job.organization else 'Unknown'}"
                        f"\n  Current: ${current_min:,.0f} - ${current_max:,.0f}" if current_min else f"\n  Current: Not set"
                        f"\n  Extracted: ${new_min:,.0f} - ${new_max:,.0f} {new_currency}"
                        f"\n  Reason: {reason}"
                    )

                    if not dry_run:
                        job.salary_min = new_min
                        job.salary_max = new_max
                        job.salary_currency = new_currency
                        job.save(update_fields=["salary_min", "salary_max", "salary_currency", "updated_at"])
                        self.stdout.write(self.style.SUCCESS("  ✓ Updated"))
                    else:
                        self.stdout.write(self.style.WARNING("  [DRY RUN] Would update"))

                    stats["updated"] += 1
                else:
                    stats["already_correct"] += 1

            except Exception as e:
                stats["errors"] += 1
                self.stdout.write(self.style.ERROR(f"Error processing job {job.id}: {e}"))

        # Summary
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(f"Processed: {stats['processed']}")
        self.stdout.write(self.style.SUCCESS(f"Updated: {stats['updated']}"))
        self.stdout.write(f"No salary found in description: {stats['no_salary_found']}")
        self.stdout.write(f"Already correct: {stats['already_correct']}")
        if stats["errors"]:
            self.stdout.write(self.style.ERROR(f"Errors: {stats['errors']}"))

        if dry_run and stats["updated"] > 0:
            self.stdout.write(self.style.WARNING(f"\nRun without --dry-run to apply {stats['updated']} updates"))
