"""
Check for stale jobs that have been removed from source platforms.
Marks them as inactive.

Two modes:
- Quick (default): HEAD request to check if URL returns 200
- Deep (--deep): GET request + check for Apply button and closed indicators
"""
import re
import time
import requests
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from jobs.models import Job


class Command(BaseCommand):
    help = "Check jobs against source platforms and mark stale ones inactive"

    # Patterns that indicate a job is still open
    APPLY_PATTERNS = [
        r'apply\s*(now|today|here)?',
        r'submit\s*(your\s*)?(application|resume|cv)',
        r'(start|begin)\s*application',
        r'apply\s*for\s*this\s*(job|position|role)',
        r'<button[^>]*>.*?apply.*?</button>',
        r'class="[^"]*apply[^"]*"',
        r'id="[^"]*apply[^"]*"',
        r'href="[^"]*apply[^"]*"',
    ]
    
    # Patterns that indicate a job is closed/filled
    CLOSED_PATTERNS = [
        r'position\s*(has\s*been\s*)?(filled|closed)',
        r'no\s*longer\s*(accepting|available|open)',
        r'this\s*(job|position|role)\s*(is\s*)?(no\s*longer|has\s*been)\s*(available|open|closed)',
        r'application\s*(period|window)\s*(has\s*)?(closed|ended)',
        r'we\s*are\s*no\s*longer\s*accepting',
        r'this\s*posting\s*(has\s*)?(expired|closed)',
        r'job\s*(has\s*been\s*)?(removed|deleted|closed)',
        r'sorry.*position.*filled',
        r'role\s*has\s*been\s*filled',
        r'vacancy\s*(has\s*been\s*)?(filled|closed)',
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Maximum number of jobs to check (default: 200)",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.5,
            help="Delay between requests in seconds (default: 0.5)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Just check, don't mark inactive",
        )
        parser.add_argument(
            "--deep",
            action="store_true",
            help="Deep check: verify Apply button exists and job not marked as closed",
        )

    def check_page_content(self, html, url):
        """
        Analyze page content to determine if job is still open.
        Returns (is_valid, reason)
        """
        html_lower = html.lower()
        
        # Check for closed/filled indicators first
        for pattern in self.CLOSED_PATTERNS:
            if re.search(pattern, html_lower, re.IGNORECASE):
                return False, "closed_indicator"
        
        # Check for apply button/link
        has_apply = False
        for pattern in self.APPLY_PATTERNS:
            if re.search(pattern, html_lower, re.IGNORECASE):
                has_apply = True
                break
        
        # Special handling for known platforms
        if 'greenhouse.io' in url or 'boards.greenhouse' in url:
            # Greenhouse has specific structure
            if 'application-apply' in html_lower or 'apply for this job' in html_lower:
                has_apply = True
        elif 'lever.co' in url:
            # Lever has specific structure
            if 'apply for this job' in html_lower or 'lever-application' in html_lower:
                has_apply = True
        elif 'ashbyhq.com' in url:
            if 'apply-button' in html_lower or 'apply now' in html_lower:
                has_apply = True
        elif 'idealist.org' in url:
            if 'listing-apply-button' in html_lower or 'apply on org' in html_lower:
                has_apply = True
        elif 'reliefweb.int' in url:
            # ReliefWeb may not have apply button on page
            if 'how-to-apply' in html_lower or 'how to apply' in html_lower:
                has_apply = True
        elif 'climatebase' in url:
            if 'apply-btn' in html_lower or 'job-apply' in html_lower:
                has_apply = True
        
        if not has_apply:
            return False, "no_apply_button"
        
        return True, "valid"

    def handle(self, *args, **options):
        limit = options["limit"]
        delay = options["delay"]
        dry_run = options["dry_run"]
        deep_check = options["deep"]

        # Sources we can verify
        verifiable_sources = [
            "greenhouse", "lever", "ashby", 
            "idealist", "reliefweb", "climatebase",
            "charityjob", "probablygood", "80000hours"
        ]

        # Get jobs that haven't been checked recently
        # Prioritize jobs not checked in the last 24 hours
        one_day_ago = timezone.now() - timezone.timedelta(days=1)
        
        jobs = (
            Job.objects.filter(
                source__in=verifiable_sources,
                is_active=True,
            )
            .exclude(
                raw_data__last_stale_check__gte=one_day_ago.isoformat()
            )
            .order_by("updated_at")[:limit]
        )

        total = jobs.count()
        mode = "deep" if deep_check else "quick"
        self.stdout.write(f"Checking {total} jobs for staleness ({mode} mode)...")

        checked = 0
        marked_inactive = 0
        errors = 0
        reasons = {}  # Track why jobs were marked inactive

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        for job in jobs:
            url = job.application_url
            if not url:
                continue

            try:
                if deep_check:
                    # Full GET request to analyze content
                    response = requests.get(
                        url, 
                        headers=headers, 
                        timeout=15, 
                        allow_redirects=True
                    )
                else:
                    # Quick HEAD request
                    response = requests.head(
                        url, 
                        headers=headers, 
                        timeout=10, 
                        allow_redirects=True
                    )
                
                checked += 1
                is_stale = False
                stale_reason = None

                # Check HTTP status
                if response.status_code in [404, 410]:
                    is_stale = True
                    stale_reason = f"http_{response.status_code}"
                elif response.status_code == 403:
                    # Some sites block HEAD requests, try GET if in deep mode
                    if not deep_check:
                        is_stale = True
                        stale_reason = "http_403"
                elif response.status_code == 200 and deep_check:
                    # Check page content for apply button
                    is_valid, reason = self.check_page_content(response.text, url)
                    if not is_valid:
                        is_stale = True
                        stale_reason = reason
                elif response.status_code >= 400:
                    is_stale = True
                    stale_reason = f"http_{response.status_code}"

                if is_stale:
                    if not dry_run:
                        job.is_active = False
                        job.raw_data = job.raw_data or {}
                        job.raw_data["crawl_error"] = f"Stale check: {stale_reason}"
                        job.raw_data["last_stale_check"] = timezone.now().isoformat()
                        job.raw_data["stale_reason"] = stale_reason
                        job.save()
                    marked_inactive += 1
                    reasons[stale_reason] = reasons.get(stale_reason, 0) + 1
                    self.stdout.write(f"  ✗ [{job.source}] {job.title[:40]}... ({stale_reason})")
                else:
                    # Job still valid - update check timestamp
                    if not dry_run:
                        job.raw_data = job.raw_data or {}
                        job.raw_data["last_stale_check"] = timezone.now().isoformat()
                        if "stale_reason" in job.raw_data:
                            del job.raw_data["stale_reason"]
                        job.save(update_fields=["raw_data"])

            except requests.Timeout:
                errors += 1
                if not dry_run:
                    job.raw_data = job.raw_data or {}
                    job.raw_data["last_stale_check"] = timezone.now().isoformat()
                    job.raw_data["last_stale_check_error"] = "timeout"
                    job.save(update_fields=["raw_data"])
            except requests.RequestException as e:
                errors += 1
                if not dry_run:
                    job.raw_data = job.raw_data or {}
                    job.raw_data["last_stale_check"] = timezone.now().isoformat()
                    job.raw_data["last_stale_check_error"] = str(e)[:100]
                    job.save(update_fields=["raw_data"])

            time.sleep(delay)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Checked: {checked}, Marked inactive: {marked_inactive}, Errors: {errors}"
        ))
        
        if reasons:
            self.stdout.write("\nReasons breakdown:")
            for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
                self.stdout.write(f"  {reason}: {count}")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("(Dry run - no changes made)"))
