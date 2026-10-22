"""Review queue, not an email sender, contact scraper or paid-demand counter."""
import json
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from growth.models import EmployerPilot
from jobs.models import EmployerOutreach, OutreachSend
from jobs.services.outreach import outreach_targets


class Command(BaseCommand):
    help = "Prepare up to 15 evidence-backed employer discovery drafts; never sends or creates quotes."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--days", type=int, default=28)
        parser.add_argument("--apply", action="store_true", help="Create internal inquiry rows only.")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 15 or not 1 <= options["days"] <= 90:
            raise CommandError("limit must be 1–15 and days 1–90.")
        # Overfetch so suppression does not silently reduce the requested sample.
        candidates = outreach_targets(days=options["days"], limit=100)
        recently_contacted = set(OutreachSend.objects.filter(
            sent_at__gte=timezone.now()-timedelta(days=90), organization_id__isnull=False,
        ).values_list("organization_id", flat=True))
        rows = []
        for row in candidates:
            if row["status"] != EmployerOutreach.Status.NEW:
                continue  # Includes opt-outs, contacted prospects, replies and historical buyers.
            org = row["org"]
            if org.pk in recently_contacted:
                continue  # Historical campaign sends can predate the manual CRM status.
            if EmployerPilot.objects.filter(organization=org).exists():
                continue
            if not row["views"] and not row["clicks"]:
                continue  # Do not manufacture an engagement story from no observations.
            if options["apply"]:
                EmployerPilot.objects.create(organization=org)
            rows.append({"organization_id": org.pk, "organization": org.name,
                         "website": org.website, "live_roles": row["live_roles"],
                         "recorded_views": row["views"], "application_link_clicks": row["clicks"],
                         "subject": f"A question about remote hiring at {org.name}",
                         "draft": f"Hi, I run RemoteImpact. We currently list {row['live_roles']} of your open roles. "
                                  f"Over the last {options['days']} days we recorded {row['views']} listing views and "
                                  f"{row['clicks']} application-link clicks across those roles; these are not confirmed applications. "
                                  "I'd like to understand which remote roles are hardest for you to fill and whether a small, "
                                  "clearly scoped hiring campaign would help. Would you be open to a brief conversation? "
                                  "No problem if not—let me know and I won't follow up. — Dipesh, RemoteImpact",
                         "review_required": "Verify current role, appropriate recipient, prior contact and permission before sending. No contact inferred from job-application email."})
            if len(rows) == options["limit"]:
                break
        self.stdout.write(json.dumps({"sent": 0, "dry_run": not options["apply"], "prospects": rows,
                                      "caveat": "Engagement can repeat across days/roles. No applications, hires or willingness to pay established."}, indent=2))
