"""List organizations worth contacting about featured listings.

Writes a CSV ranked by the last 30 days of engagement with each organization's
live roles, including a private stats link to share. It never sends email:
outreach should be personal and done by a person.

Usage:
    python manage.py employer_outreach_report --out /tmp/outreach.csv
    python manage.py employer_outreach_report --min-views 20 --limit 100
"""
import csv
import sys
from collections import defaultdict

from django.core.management.base import BaseCommand

from jobs.models import Job
from jobs.services import job_stats
from jobs.views.employers import report_url


class Command(BaseCommand):
    help = "CSV of organizations ranked by engagement, with private report links (no emails sent)."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="")
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--min-views", type=int, default=0)
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        jobs = list(
            Job.objects.filter(Job.availability_filter())
            .select_related("organization")
            .only("id", "title", "slug", "application_email", "application_url", "featured_until",
                  "organization__id", "organization__name", "organization__website", "organization__slug")
        )
        totals = job_stats.totals([job.pk for job in jobs], days=options["days"])
        by_org = defaultdict(lambda: {"views": 0, "clicks": 0, "scout": 0, "jobs": [], "emails": set()})
        for job in jobs:
            row = totals.get(job.pk, {})
            entry = by_org[job.organization]
            entry["views"] += row.get("views") or 0
            entry["clicks"] += row.get("apply_clicks") or 0
            entry["scout"] += row.get("scout_deliveries") or 0
            entry["jobs"].append(job)
            if job.application_email:
                entry["emails"].add(job.application_email)
        ranked = sorted(
            ((org, data) for org, data in by_org.items() if data["views"] >= options["min_views"]),
            key=lambda item: (item[1]["views"] + 3 * item[1]["clicks"] + 2 * item[1]["scout"]),
            reverse=True,
        )[: options["limit"]]
        handle = open(options["out"], "w", newline="") if options["out"] else sys.stdout
        writer = csv.writer(handle)
        writer.writerow(["organization", "website", "live_roles", "featured_roles", "views", "apply_clicks",
                         "scout_matches", "top_role", "contact_emails_from_listings", "private_report_link"])
        for org, data in ranked:
            top = max(data["jobs"], key=lambda job: (totals.get(job.pk, {}).get("views") or 0))
            writer.writerow([
                org.name, org.website, len(data["jobs"]), sum(1 for job in data["jobs"] if job.is_sponsored),
                data["views"], data["clicks"], data["scout"], top.title, "; ".join(sorted(data["emails"]))[:200],
                report_url(org),
            ])
        if options["out"]:
            handle.close()
            self.stdout.write(self.style.SUCCESS(f"Wrote {len(ranked)} organizations to {options['out']}"))
