"""Apply researched homepages/careers pages from a JSON file, keeping only ones that pass the same checks.

Input: [{"id": 12, "homepage": "https://...", "careers": "https://..."}]. A homepage is kept only when it is not a
job board or profile, loads, and its page title or site name names the organization (or the domain does).
Dry run unless --apply.
"""
import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import Organization
from jobs.services.org_sites import domain_matches, employer_host, host_of, page_names_org
from jobs.services.safe_fetch import fetch_public_html

ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "bamboohr.com", "recruitee.com",
             "personio.de", "personio.com", "breezy.hr", "smartrecruiters.com", "myworkdayjobs.com", "teamtailor.com",
             "applytojob.com", "jobvite.com", "rippling.com", "pinpointhq.com", "dover.com", "paylocity.com")


class Command(BaseCommand):
    help = "Verify and apply researched organization sites (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        rows = json.load(open(options["path"]))
        orgs = Organization.objects.in_bulk([r["id"] for r in rows if r.get("id")])
        kept = rejected = careers_kept = 0
        for row in rows:
            org = orgs.get(row.get("id"))
            if org is None:
                continue
            update = {}
            homepage = (row.get("homepage") or "").strip()
            host = host_of(homepage)
            if homepage and not org.homepage_url and employer_host(host):
                page = fetch_public_html(f"https://{host}/", max_bytes=800_000, total_timeout=12)
                if page.status == "ok" and (page_names_org(page.text, org.name) or domain_matches(host, org.name)):
                    update.update(homepage_url=f"https://{host}/", homepage_source="search")
                    kept += 1
                else:
                    rejected += 1
                    self.stdout.write(f"rejected {org.name}: {homepage} ({page.status})")
            careers = (row.get("careers") or "").strip()
            home_host = host_of(update.get("homepage_url") or org.homepage_url)
            careers_host = host_of(careers)
            on_own_site = bool(home_host) and (careers_host == home_host or careers_host.endswith("." + home_host))
            on_ats = any(careers_host == d or careers_host.endswith("." + d) for d in ATS_HOSTS)
            if careers.startswith("https://") and not org.careers_url and (on_own_site or on_ats):
                page = fetch_public_html(careers, max_bytes=1_500_000, total_timeout=12)
                if page.status == "ok":
                    update["careers_url"] = careers
                    careers_kept += 1
            if update and options["apply"]:
                update["sites_checked_at"] = timezone.now()
                Organization.objects.filter(pk=org.pk).update(**update)
        verb = "applied" if options["apply"] else "would apply"
        self.stdout.write(f"{verb}: homepages {kept} (rejected {rejected}), careers pages {careers_kept}")
