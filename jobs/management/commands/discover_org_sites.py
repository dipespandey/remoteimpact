"""Record each live employer's own homepage and careers page (re-checked monthly)."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from jobs.models import Organization, OrganizationClaim
from jobs.services.job_service import JobService
from jobs.services.org_sites import careers_url_for, guess_homepage, host_of, resolve_homepage


class Command(BaseCommand):
    help = "Find employers' homepages and careers pages from their records, claims and live roles."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=1500)
        parser.add_argument("--recheck-days", type=int, default=30)

    def handle(self, *args, **options):
        live = JobService.get_available_jobs()
        stale = timezone.now() - timedelta(days=options["recheck_days"])
        orgs = (
            Organization.objects.filter(pk__in=live.values("organization_id"))
            .exclude(name__iexact="Unknown Organization")
            .filter(Q(sites_checked_at__isnull=True) | Q(sites_checked_at__lt=stale))
            .order_by("sites_checked_at", "name")[: options["limit"]]
        )
        counts = {"checked": 0, "homepage": 0, "careers": 0}
        for org in orgs:
            urls = list(live.filter(organization=org).exclude(application_url="").values_list("application_url", flat=True)[:20])
            claims = [host_of("//" + e.split("@", 1)[1]) for e in OrganizationClaim.objects.filter(
                organization=org, verified_at__isnull=False).values_list("email", flat=True)]
            homepage, source = resolve_homepage(org, urls, claims)
            careers = careers_url_for(urls)
            if not homepage:
                homepage = guess_homepage(org, careers)
                source = "guessed" if homepage else ""
            Organization.objects.filter(pk=org.pk).update(
                homepage_url=homepage, homepage_source=source, careers_url=careers, sites_checked_at=timezone.now())
            counts["checked"] += 1
            counts["homepage"] += bool(homepage)
            counts["careers"] += bool(careers)
        self.stdout.write(", ".join(f"{k} {v}" for k, v in counts.items()))
