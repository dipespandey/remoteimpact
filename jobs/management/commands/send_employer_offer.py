"""Show unclaimed employers what Remote Impact already sent them, and offer a $49 feature.

Only organizations whose live roles got apply clicks from us in the last 30 days are eligible, so
every email opens with evidence. Anyone reached by an earlier employer campaign is skipped: those
emails promised no further contact after "no thanks", and replies are not recorded here.

Dry run by default. Pass --send; --limit caps one batch (default 25, about one week of hand-selling).
"""
from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.template.loader import render_to_string
from django.urls import reverse

from jobs.management.commands.send_employer_monthly_reports import claim_stats
from jobs.management.commands.send_relaunch_email import site_email
from jobs.models import Organization, OutreachSend
from jobs.services.email_service import email_service
from jobs.services.job_service import JobService

CAMPAIGN = "employer-offer-2026-11"
LOOKUP_TTL = 30 * 86400


def public_role_address(org):
    """site_email, remembered for 30 days so weekly runs only fetch organizations they have not seen."""
    key = f"employer-offer:site-email:{org.pk}"
    found = cache.get(key)
    if found is None:
        found = site_email(org)
        cache.set(key, found, LOOKUP_TTL)
    return found


def prospects(min_clicks):
    contacted = OutreachSend.objects.filter(organization__isnull=False)
    contacted_orgs = set(contacted.values_list("organization_id", flat=True))
    org_ids = JobService.get_available_jobs().values_list("organization_id", flat=True).distinct()
    orgs = (Organization.objects.filter(pk__in=org_ids, members__isnull=True)
            .exclude(pk__in=contacted_orgs).exclude(homepage_url="").exclude(slug=""))
    rows = [(org, claim_stats(org)) for org in orgs]
    rows = [(org, stats) for org, stats in rows if stats["clicks"] >= min_clicks and stats["top_slug"]]
    return sorted(rows, key=lambda r: (-r[1]["clicks"], -r[1]["views"])), {e.lower() for e in contacted.values_list("email", flat=True)}


class Command(BaseCommand):
    help = "Email unclaimed employers their 30-day Remote Impact results with a $49 feature offer. Dry run unless --send."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true")
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument("--min-clicks", type=int, default=1)

    def handle(self, *args, **options):
        site = settings.SITE_URL.rstrip("/")
        rows, contacted_emails = prospects(options["min_clicks"])
        reached = 0
        for org, stats in rows:
            if reached >= options["limit"]:
                break
            email = public_role_address(org)
            if not email or email.lower() in contacted_emails:
                self.stdout.write(f"Skip {org.name}: {'already contacted' if email else 'no public role address'}")
                continue
            context = {"org": org, "stats": stats,
                       "feature_url": f"{site}{reverse('jobs:feature_job', kwargs={'slug': stats['top_slug']})}?utm_source=email&utm_campaign={CAMPAIGN}",
                       "claim_url": f"{site}{reverse('jobs:claim_organization', kwargs={'slug': org.slug})}?utm_source=email&utm_campaign={CAMPAIGN}"}
            subject = f"{org.name}: {stats['clicks']} applicant click{'s' if stats['clicks'] != 1 else ''} from Remote Impact"
            if not options["send"]:
                self.stdout.write(f"Would email {org.name} at {email}: {stats['views']} views, {stats['clicks']} clicks, top '{stats['top']}'")
                reached += 1
                continue
            try:
                OutreachSend.objects.create(campaign=CAMPAIGN, email=email.lower(), organization=org)
            except IntegrityError:
                continue
            if not email_service.send_email(to=email, subject=subject,
                                            html=render_to_string("emails/employer_offer.html", context),
                                            text=render_to_string("emails/employer_offer.txt", context)):
                OutreachSend.objects.filter(campaign=CAMPAIGN, email=email.lower()).delete()
                continue
            contacted_emails.add(email.lower())
            reached += 1
        verb = "Sent" if options["send"] else "Dry run: would send"
        self.stdout.write(f"{verb} {reached} of {len(rows)} eligible organizations.")
