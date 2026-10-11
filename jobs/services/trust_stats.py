"""Numbers behind the "every listing is checked" promise, for the homepage and /how-we-check/."""
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from jobs.models import Job

CACHE_KEY = "trust_stats:v2"
REMOVED_STATUSES = ["closed", "not_remote", "dead"]


def trust_stats(refresh=False):
    stats = None if refresh else cache.get(CACHE_KEY)
    if stats is not None:
        return stats
    from jobs.services.job_service import JobService

    now = timezone.now()
    available = JobService.get_available_jobs()
    live = JobService.count_openings()  # distinct openings, as the job list counts them
    listings = available.count()  # every listing, including regional reposts: the base for the shares
    confirmed = available.filter(
        raw_data__source_verification__method="ats_api",
        raw_data__source_verification__status="open",
    ).count()
    with_salary = available.filter(Q(salary_min__isnull=False) | Q(salary_max__isnull=False)).count()

    week_ago = (now - timedelta(days=7)).isoformat()
    removed = {"closed": 0, "not_remote": 0, "left_feed": 0}
    rows = Job.objects.filter(is_active=False).filter(
        Q(raw_data__source_verification__status__in=REMOVED_STATUSES) | Q(raw_data__has_key="removed_from_source_at")
    ).values_list("raw_data", flat=True)
    for raw in rows.iterator(chunk_size=2000):
        raw = raw or {}
        check = raw.get("source_verification") or {}
        if check.get("status") in REMOVED_STATUSES and str(check.get("checked_at") or "") >= week_ago:
            removed["not_remote" if check["status"] == "not_remote" else "closed"] += 1
        elif str(raw.get("removed_from_source_at") or "") >= week_ago:
            removed["left_feed"] += 1

    stats = {
        "live": live,
        "confirmed_open": confirmed,
        "listings": listings,
        "confirmed_share": round(100 * confirmed / listings) if listings else 0,
        "organizations": available.values("organization").distinct().count(),
        "salary_share": round(100 * with_salary / listings) if listings else 0,
        "removed_7d": sum(removed.values()),
        "removed_closed_7d": removed["closed"],
        "removed_not_remote_7d": removed["not_remote"],
        "removed_left_feed_7d": removed["left_feed"],
        "computed_at": now,
    }
    cache.set(CACHE_KEY, stats, 60 * 60)
    return stats
