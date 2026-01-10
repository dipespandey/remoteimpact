"""
Management command to normalize job location data.

Converts messy location strings (addresses, city/state combos, garbage data)
into standardized country/region values for better filtering.

Usage:
    python manage.py normalize_locations                  # Normalize all locations
    python manage.py normalize_locations --dry-run       # Preview without changes
    python manage.py normalize_locations --show-mapping  # Show current -> normalized mappings
"""
from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from jobs.models import Job
from jobs.services.location_normalizer import normalize_location


class Command(BaseCommand):
    help = "Normalize job location data to standard country/region values."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without making them.",
        )
        parser.add_argument(
            "--show-mapping",
            action="store_true",
            help="Show the mapping of current to normalized locations.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of jobs to process.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        show_mapping = options["show_mapping"]
        limit = options["limit"]

        jobs = Job.objects.filter(is_active=True)
        if limit:
            jobs = jobs[:limit]

        jobs = list(jobs)

        self.stdout.write(f"Processing {len(jobs)} active jobs...")

        # Track changes
        changes = []
        location_mapping = {}  # original -> normalized
        normalized_counts = Counter()

        for job in jobs:
            original = job.location
            normalized = normalize_location(original)

            if original not in location_mapping:
                location_mapping[original] = normalized

            normalized_counts[normalized] += 1

            if original != normalized:
                changes.append({
                    "job_id": job.id,
                    "title": job.title[:50],
                    "original": original[:80] if original else "(empty)",
                    "normalized": normalized,
                })

        if show_mapping:
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write("LOCATION MAPPING (Original -> Normalized)")
            self.stdout.write("=" * 80)

            # Sort by normalized value for easier review
            sorted_mapping = sorted(location_mapping.items(), key=lambda x: (x[1], x[0]))
            for original, normalized in sorted_mapping:
                if original != normalized:
                    self.stdout.write(f"  {original[:60]:<60} -> {normalized}")

            self.stdout.write("\n" + "=" * 80)
            self.stdout.write("NORMALIZED LOCATION COUNTS")
            self.stdout.write("=" * 80)
            for loc, count in normalized_counts.most_common():
                self.stdout.write(f"  {count:5} | {loc}")

            return

        self.stdout.write(f"\nFound {len(changes)} locations that need normalization")

        # Show sample changes
        if changes:
            self.stdout.write("\nSample changes (first 20):")
            for change in changes[:20]:
                self.stdout.write(
                    f"  [{change['job_id']}] {change['original'][:50]} -> {change['normalized']}"
                )
            if len(changes) > 20:
                self.stdout.write(f"  ... and {len(changes) - 20} more")

        # Show normalized distribution
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write("RESULT DISTRIBUTION (after normalization)")
        self.stdout.write("=" * 50)
        for loc, count in normalized_counts.most_common():
            self.stdout.write(f"  {count:5} | {loc}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDRY RUN - no changes made"))
            return

        # Apply changes
        self.stdout.write("\nApplying changes...")

        updated_count = 0
        with transaction.atomic():
            for job in jobs:
                original = job.location
                normalized = normalize_location(original)

                if original != normalized:
                    # Store original in raw_data for reference
                    raw_data = job.raw_data or {}
                    raw_data["original_location"] = original
                    job.raw_data = raw_data
                    job.location = normalized
                    job.save(update_fields=["location", "raw_data"])
                    updated_count += 1

        self.stdout.write(self.style.SUCCESS(f"\nUpdated {updated_count} job locations"))

        # Show final distribution
        final_counts = Counter(
            Job.objects.filter(is_active=True).values_list("location", flat=True)
        )
        self.stdout.write("\nFinal location distribution:")
        for loc, count in final_counts.most_common():
            self.stdout.write(f"  {count:5} | {loc}")
