"""Free featured windows that show employers what a $49 feature does for one of their roles.

A trial sets the same `Job.featured_until` a purchase does, so placement code needs no branch.
Paid features always outrank trials in a featured slot, and only paid ones say "by the employer".
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Exists, OuterRef, Sum
from django.utils import timezone

from jobs.models import FeatureTrial, Job, JobDailyStat, JobFeaturePurchase


def active_trials(now=None):
    return FeatureTrial.objects.filter(ends_at__gt=now or timezone.now())


def paid_first(queryset, now=None):
    """Order featured jobs so a paying employer is never pushed out by a trial."""
    trial = active_trials(now).filter(job=OuterRef("pk"))
    return queryset.annotate(on_trial=Exists(trial)).order_by("on_trial", "featured_until")


def is_trial(job, now=None):
    return active_trials(now).filter(job=job).exists()


def candidates(limit, *, min_clicks=10, days=30):
    """The most-clicked live role at each of the employers we send the most applicants to."""
    from jobs.services.job_service import JobService
    since = timezone.localdate() - timedelta(days=days - 1)
    live = JobService.get_available_jobs()
    paid_orgs = set(JobFeaturePurchase.objects.values_list("job__organization_id", flat=True))
    tried_orgs = set(FeatureTrial.objects.values_list("job__organization_id", flat=True))
    featured_orgs = set(live.filter(Job.sponsored_filter()).values_list("organization_id", flat=True))
    rows = (JobDailyStat.objects.filter(date__gte=since, job__in=live)
            .values("job_id", "job__organization_id").annotate(clicks=Sum("apply_clicks"))
            .order_by("-clicks", "job_id"))
    picked, seen = [], paid_orgs | tried_orgs | featured_orgs
    org_clicks = {}
    for row in rows:
        org_clicks[row["job__organization_id"]] = org_clicks.get(row["job__organization_id"], 0) + (row["clicks"] or 0)
    for row in rows:
        org = row["job__organization_id"]
        if org in seen or org_clicks[org] < min_clicks:
            continue
        seen.add(org)
        picked.append((row["job_id"], org_clicks[org]))
    picked.sort(key=lambda item: -item[1])
    return [(Job.objects.select_related("organization").get(pk=job_id), clicks) for job_id, clicks in picked[:limit]]


def start(job, days, now=None):
    now = now or timezone.now()
    ends = now + timedelta(days=days)
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job.pk)
        if job.featured_until and job.featured_until > now:
            return None
        Job.objects.filter(pk=job.pk).update(featured_until=ends)
        return FeatureTrial.objects.create(job=job, started_at=now, ends_at=ends)


def _window(job_ids, start, end):
    stats = JobDailyStat.objects.filter(date__gte=start, date__lt=end)
    if job_ids is not None:
        stats = stats.filter(job_id__in=job_ids)
    totals = stats.aggregate(
        views=Sum("views"), clicks=Sum("apply_clicks"))
    return totals["views"] or 0, totals["clicks"] or 0


def results(trials):
    """Each role's views and clicks during its trial against the same number of days just before.

    Site-wide clicks over the same two windows say how much of any change is just the season.
    """
    rows = []
    for trial in trials:
        start, end = trial.started_at.date(), min(trial.ends_at, timezone.now()).date()
        days = max((end - start).days, 1)
        before = _window([trial.job_id], start - timedelta(days=days), start)
        during = _window([trial.job_id], start, end)
        rows.append({"trial": trial, "days": days, "before": before, "during": during})
    if not rows:
        return rows, None
    start = min(r["trial"].started_at.date() for r in rows)
    days = max(r["days"] for r in rows)
    site_before = _window(None, start - timedelta(days=days), start)[1]
    site_during = _window(None, start, start + timedelta(days=days))[1]
    return rows, (site_during / site_before if site_before else None)
