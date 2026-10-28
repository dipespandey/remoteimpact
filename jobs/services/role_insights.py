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
    from jobs.api.serialize import salary
    pay = salary(job)
    if not pay or pay['currency'] != 'USD':
        return None
    values = [float(v) for v in (pay.get('min'), pay.get('max')) if v is not None]
    if not values:
        return None
    mid = sum(values) / len(values)
    return mid if 10_000 <= mid <= 2_000_000 else None


def category_stats(category_id, job_type):
    """Open-role count and annual USD midpoints for a category and job type (cached 30 min)."""
    key = f"role-insights:v2:{category_id}:{job_type}"
    stats = cache.get(key)
    if stats is None:
        jobs = JobService.get_available_jobs().filter(category_id=category_id, job_type=job_type)
        # Use the public source-evidence gate for the role and every peer alike.
        rows = list(jobs.select_related('organization', 'category'))
        mids = [mid for job in rows if (mid := _annual(job)) is not None]
        week_ago = timezone.now() - timedelta(days=7)
        stats = {"open": len(rows), "new_7d": sum(1 for job in rows if job.posted_at and job.posted_at >= week_ago), "mids": sorted(mids)}
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
