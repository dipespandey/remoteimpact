"""Fetch logos for organizations with live roles from their own websites.

Re-checks an organization at most every 30 days; organizations with an uploaded logo are skipped.
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from jobs.models import Organization, OrganizationLogo
from jobs.services.job_service import JobService
from jobs.services.org_logos import fetch_logo


# Job boards, applicant-tracking systems and social sites: an organization "website" on one of these
# is a profile page, and its icon is the platform's, not the employer's.
NOT_EMPLOYER_SITES = (
    "idealist.org", "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "recruitee.com", "personio.de", "personio.com",
    "bamboohr.com", "smartrecruiters.com", "myworkdayjobs.com", "breezy.hr", "climatebase.org", "reliefweb.int",
    "probablygood.org", "remoteimpact.org", "devex.com", "impactpool.org", "wellfound.com", "indeed.com",
)


def employer_site(website: str) -> bool:
    from urllib.parse import urlparse

    host = urlparse(website if "//" in website else f"//{website}").netloc.lower().removeprefix("www.")
    return bool(host) and not any(host == d or host.endswith("." + d) for d in NOT_EMPLOYER_SITES)


class Command(BaseCommand):
    help = "Fetch organization logos from their websites (live-role organizations first)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)
        # The safe fetcher's DNS timeout relies on the main thread, so the default is sequential.
        parser.add_argument("--workers", type=int, default=1)

    def handle(self, *args, **options):
        stale = timezone.now() - timedelta(days=30)
        live = JobService.get_available_jobs().values("organization_id")
        orgs = list(
            Organization.objects.filter(pk__in=live).exclude(website="")
            .filter(Q(logo="") | Q(logo__isnull=True))
            .filter(Q(logo_checked_at__isnull=True) | Q(logo_checked_at__lt=stale))
            .order_by("logo_checked_at", "name")
            .only("pk", "website", "name")[: options["limit"]]
        )
        def fetch(org):
            return org, fetch_logo(org.website) if employer_site(org.website) else None

        if options["workers"] > 1:
            with ThreadPoolExecutor(max_workers=options["workers"]) as pool:
                results = list(pool.map(fetch, orgs))
        else:
            results = [fetch(org) for org in orgs]
        found = 0
        for org, result in results:
            update = {"logo_checked_at": timezone.now()}
            if result:
                png, source = result
                OrganizationLogo.objects.update_or_create(organization=org, defaults={"png": png, "source_url": source})
                update["logo_version"] = hashlib.sha1(png).hexdigest()[:10]
                found += 1
            Organization.objects.filter(pk=org.pk).update(**update)
        self.stdout.write(f"checked {len(results)}, logos found {found}")
