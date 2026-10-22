"""Employer outreach queue: who to contact, with what numbers, and a ready email.

Nothing here sends email. Staff copy the draft into their own mail client so
every message comes from a person.
"""
from datetime import timedelta
from urllib.parse import quote, urlencode

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils import timezone

from jobs.models import EmployerOutreach, OrganizationClaim, Job, JobDailyStat, JobFeaturePurchase, Organization
from jobs.services.job_service import JobService

FEATURE_PRICE = 49


def _site():
    return getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")


def draft_email(org_name, role_title, views, clicks, days, report_link, feature_link, sender, live_roles=1):
    # The numbers cover every live role, so only name a single role when there is just one.
    target = f"your {role_title} role" if live_roles == 1 else f"your {live_roles} roles on Remote Impact"
    subject = (
        f"{clicks} application-link clicks on {target}"
        if clicks else f"{views} listing views on {target}"
    )
    body = (
        f"Hi there,\n\n"
        f"I run Remote Impact (remoteimpact.org), a job board for remote roles at climate, global health, "
        f"AI safety and other public-interest organizations. We list {target}, with a link "
        f"to the source application page and information about our listing checks.\n\n"
        f"In the last {days} days we recorded {views} listing views and {clicks} application-link clicks "
        f"across your currently live roles. These counts can include the same visitor across days or roles; "
        f"they do not confirm completed applications. Our outbound links include utm_source=remoteimpact "
        f"for referral attribution. Here is the report:\n{report_link}\n\n"
        f"If you'd like more of the right people to see it, you can feature the role for 30 days for "
        f"${FEATURE_PRICE}:\n{feature_link}\n\n"
        f"Either way the listing stays up for free while it's open. If anything in it is wrong, reply and "
        f"I'll fix it.\n\n"
        f"{sender}\nRemote Impact · remoteimpact.org"
    )
    return subject, body


def outreach_targets(days=7, status="", limit=100, sender="Dipesh"):
    """Organizations with live roles, ranked by engagement we sent them recently."""
    from jobs.views.employers import report_url

    since = timezone.localdate() - timedelta(days=days - 1)
    live = JobService.get_available_jobs().exclude(source=Job.Source.MANUAL)
    org_ids = live.values("organization_id")
    stats = {
        row["job__organization_id"]: row
        for row in JobDailyStat.objects.filter(date__gte=since, job__organization_id__in=org_ids)
        .values("job__organization_id").annotate(views=Sum("views"), clicks=Sum("apply_clicks"))
    }
    role_counts = dict(live.values_list("organization_id").annotate(n=Count("id")))
    paid_orgs = set(JobFeaturePurchase.objects.values_list("job__organization_id", flat=True)) | set(
        Job.objects.filter(is_paid=True).values_list("organization_id", flat=True)
    )
    records = {o.organization_id: o for o in EmployerOutreach.objects.all()}
    claimed = {}
    for claim in OrganizationClaim.objects.filter(verified_at__isnull=False).order_by("-domain_match", "created_at"):
        claimed.setdefault(claim.organization_id, claim.email)

    candidates = sorted(
        role_counts,
        key=lambda oid: (
            (stats.get(oid, {}).get("clicks") or 0) * 3 + (stats.get(oid, {}).get("views") or 0),
            role_counts[oid],
        ),
        reverse=True,
    )
    orgs = Organization.objects.in_bulk(candidates[: limit * 3])
    rows = []
    for oid in candidates:
        org = orgs.get(oid)
        if org is None or org.name.lower() == "unknown organization":
            continue
        record = records.get(oid)
        row_status = record.status if record else EmployerOutreach.Status.NEW
        if oid in paid_orgs and row_status != EmployerOutreach.Status.WON:
            row_status = EmployerOutreach.Status.WON
        if status and row_status != status:
            continue
        views = stats.get(oid, {}).get("views") or 0
        clicks = stats.get(oid, {}).get("clicks") or 0
        org_jobs = live.filter(organization_id=oid)
        top = (
            org_jobs.annotate(c=Sum("daily_stats__apply_clicks", filter=Q(daily_stats__date__gte=since)))
            .order_by("-c", "-posted_at").first()
        )
        if top is None:
            continue
        report_link = report_url(org)
        feature_link = f"{_site()}{reverse('jobs:feature_job', kwargs={'slug': top.slug})}?source=outreach"
        subject, body = draft_email(org.name, top.title, views, clicks, days, report_link, feature_link, sender,
                                    live_roles=role_counts[oid])
        emails = sorted({e for e in org_jobs.exclude(application_email="").values_list("application_email", flat=True)})
        to = claimed.get(oid) or (record.contact if record and "@" in (record.contact or "") else "") or (emails[0] if emails else "")
        rows.append({
            "org": org, "views": views, "clicks": clicks, "live_roles": role_counts[oid], "top": top,
            "status": row_status, "record": record, "report_link": report_link, "feature_link": feature_link,
            "subject": subject, "body": body, "listing_emails": emails, "claimed_by": claimed.get(oid, ""),
            "mailto": f"mailto:{quote(to)}?{urlencode({'subject': subject, 'body': body}, quote_via=quote)}",
            "linkedin": "https://www.linkedin.com/search/results/people/?" + urlencode(
                {"keywords": f"{org.name} recruiter"}, quote_via=quote),
        })
        if len(rows) >= limit:
            break
    return rows
