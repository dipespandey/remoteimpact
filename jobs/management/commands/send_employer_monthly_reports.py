"""Monthly 'applicants we sent you' email to verified organization claims.

Dry run unless --send. Not scheduled; run by hand until the first batch is reviewed.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from jobs.models import JobDailyStat, OrganizationClaim
from jobs.services.email_service import email_service
from jobs.services.job_service import JobService
from jobs.views.employers import report_url


def claim_stats(organization):
    since = timezone.localdate() - timedelta(days=29)
    live = JobService.get_available_jobs().filter(organization=organization)
    rows = (
        JobDailyStat.objects.filter(date__gte=since, job__in=live)
        .values("job__title").annotate(views=Sum("views"), clicks=Sum("apply_clicks"))
        .order_by("-clicks", "-views")
    )
    rows = list(rows)
    return {
        "roles": live.count(),
        "views": sum(r["views"] or 0 for r in rows),
        "clicks": sum(r["clicks"] or 0 for r in rows),
        "top": rows[0]["job__title"] if rows and rows[0]["clicks"] else "",
    }


class Command(BaseCommand):
    help = "Email verified employer contacts their last-30-day stats (dry run unless --send)."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=25)
        claims = OrganizationClaim.objects.filter(verified_at__isnull=False, monthly_report=True).select_related("organization")
        sent = skipped = 0
        for claim in claims:
            if claim.last_report_sent_at and claim.last_report_sent_at > cutoff:
                skipped += 1
                continue
            stats = claim_stats(claim.organization)
            if not stats["roles"]:
                skipped += 1
                continue
            self.stdout.write(f"{claim.email} · {claim.organization.name}: {stats}")
            if options["send"] and email_service.send_employer_monthly_report(claim, stats, report_url(claim.organization)):
                claim.last_report_sent_at = timezone.now()
                claim.save(update_fields=["last_report_sent_at"])
                sent += 1
        self.stdout.write(f"{'sent' if options['send'] else 'would send'} {sent if options['send'] else claims.count() - skipped}, skipped {skipped}")
