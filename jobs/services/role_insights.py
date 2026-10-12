"""Facts we hold about a role that the employer's own posting doesn't carry.

Everything here is computed from our own live listings and verification checks, never invented: how the
pay compares with similar open roles, how many similar roles are open, how the employer's other roles look,
and when the employer's own board last confirmed this one open.
"""
from datetime import timedelta
from statistics import median

from django.core.cache import cache
from django.utils import timezone

from jobs.models import Job
from jobs.services.job_service import JobService

MIN_PEER_SALARIES = 8


def _annual(job):
    """Annual USD midpoint, when the listing states one."""
    if job.salary_currency != "USD":
        return None
    values = [float(v) for v in (job.salary_min, job.salary_max) if v]
    if not values:
        return None
    mid = sum(values) / len(values)
    return mid if 10_000 <= mid <= 2_000_000 else None


def category_stats(category_id, job_type):
    """Open-role count and annual USD midpoints for a category and job type (cached 30 min)."""
    key = f"role-insights:v1:{category_id}:{job_type}"
    stats = cache.get(key)
    if stats is None:
        jobs = JobService.get_available_jobs().filter(category_id=category_id, job_type=job_type)
        rows = list(jobs.values_list("salary_min", "salary_max", "salary_currency", "posted_at"))
        mids = []
        for low, high, currency, _ in rows:
            fake = Job(salary_min=low, salary_max=high, salary_currency=currency)
            mid = _annual(fake)
            if mid:
                mids.append(mid)
        week_ago = timezone.now() - timedelta(days=7)
        stats = {"open": len(rows), "new_7d": sum(1 for r in rows if r[3] and r[3] >= week_ago), "mids": sorted(mids)}
        cache.set(key, stats, 30 * 60)
    return stats


def role_insights(job, category_name=""):
    """Dict of facts for the "Role at a glance" block (empty keys are simply absent)."""
    facts = {}
    if job.category_id:
        stats = category_stats(job.category_id, job.job_type)
        label = (category_name or "").strip() or "this area"
        facts["similar_open"] = stats["open"]
        facts["similar_new_7d"] = stats["new_7d"]
        facts["area"] = label
        mid = _annual(job)
        mids = stats["mids"]
        if mid and len(mids) >= MIN_PEER_SALARIES:
            below = sum(1 for m in mids if m < mid)
            facts["pay_percentile"] = round(100 * below / len(mids))
            facts["peer_median"] = round(median(mids), -3)
            facts["peer_count"] = len(mids)
            facts["pay_mid"] = round(mid, -2)
    org_open = JobService.get_available_jobs().filter(organization_id=job.organization_id).exclude(pk=job.pk).count()
    facts["org_other_open"] = org_open
    return facts
