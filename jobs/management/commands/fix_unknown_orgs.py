"""
Fix jobs with Unknown Organization by extracting org names from URLs.
"""
import re
from django.core.management.base import BaseCommand
from django.utils.text import slugify
from jobs.models import Job, Organization


class Command(BaseCommand):
    help = "Fix jobs with Unknown Organization"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Just show what would be fixed, don't save",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # Find unknown organization
        unknown_org = Organization.objects.filter(name__icontains='unknown').first()
        if not unknown_org:
            self.stdout.write("No 'Unknown Organization' found - nothing to fix!")
            return

        jobs = Job.objects.filter(organization=unknown_org, is_active=True)
        total = jobs.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No jobs with Unknown Organization!"))
            return

        self.stdout.write(f"Found {total} jobs with Unknown Organization")

        fixed = 0
        for job in jobs:
            org_name = self._extract_org_name(job)

            if org_name:
                if not dry_run:
                    org, _ = Organization.objects.get_or_create(
                        slug=slugify(org_name)[:50],
                        defaults={"name": org_name}
                    )
                    job.organization = org
                    job.save(update_fields=["organization"])

                fixed += 1
                if fixed <= 20:
                    self.stdout.write(f"  ✓ {job.title[:40]}... → {org_name}")

        self.stdout.write(self.style.SUCCESS(f"\nFixed {fixed}/{total} jobs"))
        
        if dry_run:
            self.stdout.write(self.style.WARNING("(Dry run - no changes saved)"))

    def _extract_org_name(self, job) -> str:
        """Try to extract organization name from job data."""
        url = job.application_url or ""

        # CharityJob: /jobs/{org-slug}/{title}/{id}
        if "charityjob.co.uk" in url:
            match = re.search(r'/jobs/([^/]+)/[^/]+/\d+', url)
            if match:
                return match.group(1).replace('-', ' ').title()

        # Greenhouse: boards.greenhouse.io/{company}/jobs/{id}
        if "greenhouse.io" in url:
            match = re.search(r'greenhouse\.io/([^/]+)/jobs', url)
            if match:
                return match.group(1).replace('-', ' ').title()

        # Lever: jobs.lever.co/{company}/{id}
        if "lever.co" in url:
            match = re.search(r'lever\.co/([^/]+)/', url)
            if match:
                return match.group(1).replace('-', ' ').title()

        # Ashby: jobs.ashbyhq.com/{company}/{id}
        if "ashbyhq.com" in url:
            match = re.search(r'ashbyhq\.com/([^/]+)/', url)
            if match:
                return match.group(1).replace('-', ' ').title()

        # Idealist: idealist.org/en/nonprofit-job/...{org-name}-{location}
        # This is harder to parse reliably

        # Try to extract from description
        if job.description:
            # Look for "About [Company]" or "at [Company]"
            patterns = [
                r'About\s+([A-Z][A-Za-z0-9\s\-\'&]+?)(?:\s*\n|\s+is\s|\s+was\s)',
                r'(?:work(?:ing)?|join(?:ing)?)\s+(?:at|for)\s+([A-Z][A-Za-z0-9\s\-\'&]+?)(?:\.|,|\s+(?:is|are|we|to))',
            ]
            for pattern in patterns:
                match = re.search(pattern, job.description[:1000])
                if match:
                    name = match.group(1).strip()
                    if 5 < len(name) < 80:
                        return name

        return ""
