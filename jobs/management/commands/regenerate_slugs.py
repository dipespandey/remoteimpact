"""
Regenerate job slugs to include actual job title + org name.

Usage:
    python manage.py regenerate_slugs --dry-run
    python manage.py regenerate_slugs
    python manage.py regenerate_slugs --all
"""
from __future__ import annotations

import json
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from jobs.models import Job
from jobs.utils import unique_slug


class Command(BaseCommand):
    help = "Regenerate job slugs to include actual job title"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
        parser.add_argument("--all", action="store_true", help="Regenerate all slugs, not just bad ones")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        regen_all = options["all"]

        if regen_all:
            jobs = Job.objects.select_related("organization").all()
        else:
            jobs = Job.objects.select_related("organization").filter(slug__startswith="job-at-")

        total = jobs.count()
        self.stdout.write(f"Found {total} jobs to regenerate slugs for")

        updated = 0
        skipped = 0
        redirects = {}

        for job in jobs.iterator():
            title = job.title
            # Skip placeholder titles that were never enriched
            if re.match(r'^Job at [^-@]+$', title.strip()):
                skipped += 1
                continue

            # Strip trailing source markers
            for suffix in [" - Lever", " - Jobs", " - Greenhouse", " - Workday"]:
                if title.endswith(suffix):
                    title = title[: -len(suffix)]

            # Strip "@ OrgName" or "- OrgName" from title
            org_name = job.organization.name
            title = title.replace(f" @ {org_name}", "").replace(f" - {org_name}", "").strip()

            if not title or title.startswith("Job at"):
                skipped += 1
                continue

            base_text = f"{title}-{org_name}"
            new_slug = unique_slug(Job, base_text)

            if new_slug == job.slug:
                continue

            old_slug = job.slug
            redirects[old_slug] = new_slug

            if dry_run:
                self.stdout.write(f"  {old_slug} -> {new_slug}")
            else:
                with transaction.atomic():
                    job.slug = new_slug
                    job.save(update_fields=["slug"])
                updated += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"\n[DRY RUN] Would update {len(redirects)} slugs, skip {skipped}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated {updated} slugs, skipped {skipped}"))

        # Save redirect map
        if redirects:
            redirect_path = "/tmp/slug_redirects.json"
            with open(redirect_path, "w") as f:
                json.dump(redirects, f, indent=2)
            self.stdout.write(f"Redirect map saved to {redirect_path} ({len(redirects)} entries)")
