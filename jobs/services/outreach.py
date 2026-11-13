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


def linkedin_people_search(org_name):
    """People at the organization who can claim a page or approve a feature, not only recruiters."""
    keywords = f'"{org_name}" (operations OR people OR talent OR recruiting OR hiring)'
    return "https://www.linkedin.com/search/results/people/?" + urlencode({"keywords": keywords}, quote_via=quote)


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
            "linkedin": linkedin_people_search(org.name),
        })
        if len(rows) >= limit:
            break
    return rows


LINKEDIN_NOTE_LIMIT = 300  # LinkedIn's cap on a connection-request note.
LINKEDIN_DAILY = 8
EMAIL_REST_DAYS = 30


def linkedin_note(org_name, clicks, days, claim_link, trial=None):
    """A claim-first note that fits a connection request. The first ask is free; selling comes after a reply."""
    lead = f"Hi, I run Remote Impact, a job board for remote impact roles. In the last {days} days we sent {org_name} {clicks} apply clicks, free."
    if trial:
        lift = trial.get("lift")
        lead += f" Your {trial['role']} role is featured free until {trial['ends']:%b %-d}"
        lead += f" and got {lift:.1f}x the clicks." if lift else "."
    ask = f" Your numbers and free page: {claim_link}"
    note = lead + ask
    if len(note) <= LINKEDIN_NOTE_LIMIT:
        return note
    # Drop the trial line, then shorten the name: the claim link must survive intact.
    short = "Hi, I run Remote Impact, a remote impact job board. We sent {} " + f"{clicks} apply clicks in {days} days, free." + ask
    room = LINKEDIN_NOTE_LIMIT - len(short.format(""))
    name = org_name if len(org_name) <= room else org_name[: max(room - 1, 0)].rstrip() + "…"
    return short.format(name)


def _trial_facts(now):
    """Per organization: the trial role, its end date, and its click lift when the numbers support one."""
    from jobs.services import feature_trials

    rows, _site_ratio = feature_trials.results(feature_trials.active_trials(now).select_related("job"))
    facts = {}
    for row in rows:
        trial, (_, before), (_, during) = row["trial"], row["before"], row["during"]
        lift = during / before if before and during >= 3 and during >= 1.5 * before else None
        facts[trial.job.organization_id] = {"role": trial.job.title[:60], "ends": trial.ends_at, "lift": lift}
    for trial in feature_trials.active_trials(now).select_related("job"):
        facts.setdefault(trial.job.organization_id, {"role": trial.job.title[:60], "ends": trial.ends_at, "lift": None})
    return facts


def linkedin_queue(size=LINKEDIN_DAILY, days=30, now=None):
    """Today's personal LinkedIn notes: employers on a free trial first, then by apply clicks we sent.

    Skips anyone already contacted, claimed, paid or not interested, and anyone an employer email
    campaign reached in the last 30 days. The queue shrinks as notes are marked sent today.
    """
    from jobs.models import OutreachSend

    now = now or timezone.now()
    sent_today = EmployerOutreach.objects.filter(contacted_at__date=timezone.localdate(now)).count()
    remaining = max(size - sent_today, 0)
    if not remaining:
        return [], sent_today
    emailed = {}
    for org_id, sent_at in (OutreachSend.objects.filter(organization__isnull=False, campaign__icontains="employer")
                            .values_list("organization_id", "sent_at")):
        emailed[org_id] = max(sent_at, emailed.get(org_id, sent_at))
    trials = _trial_facts(now)
    rows = []
    for row in outreach_targets(days=days, status=EmployerOutreach.Status.NEW, limit=size * 6):
        org = row["org"]
        last_email = emailed.get(org.pk)
        if row["claimed_by"] or not row["clicks"] or not org.slug:
            continue
        if last_email and last_email > now - timedelta(days=EMAIL_REST_DAYS):
            continue
        trial = trials.get(org.pk)
        claim_link = f"{_site()}{reverse('jobs:claim_organization', kwargs={'slug': org.slug})}"
        row.update(trial=trial, last_email=last_email,
                   note=linkedin_note(org.name, row["clicks"], days, claim_link, trial))
        rows.append(row)
    rows.sort(key=lambda r: (r["trial"] is None, -r["clicks"]))
    return rows[:remaining], sent_today
