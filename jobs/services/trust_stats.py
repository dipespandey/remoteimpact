"""Numbers behind the "every listing is checked" promise, for the homepage and /how-we-check/."""
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q, Count
from django.utils import timezone

from jobs.models import Job

CACHE_KEY = "trust_stats:v3"
REMOVED_STATUSES = ["closed", "not_remote", "dead"]


def trust_stats(refresh=False):
    stats = None if refresh else cache.get(CACHE_KEY)
    if stats is not None:
        return stats
    from jobs.services.job_service import JobService

    now = timezone.now()
    available = JobService.get_available_jobs()
    live = JobService.count_openings()  # distinct openings, as the job list counts them
    totals = available.aggregate(listings=Count("pk"), organizations=Count("organization_id", distinct=True), confirmed=Count("pk", filter=Q(
        raw_data__source_verification__method="ats_api",
        raw_data__source_verification__status="open",
    )), with_salary=Count("pk", filter=Q(salary_min__isnull=False) | Q(salary_max__isnull=False)))
    listings, confirmed, with_salary = totals["listings"], totals["confirmed"], totals["with_salary"]

    week_ago = (now - timedelta(days=7)).isoformat()
    checked_recently = Q(raw_data__source_verification__checked_at__gte=week_ago)
    checked_removed = checked_recently & Q(raw_data__source_verification__status__in=REMOVED_STATUSES)
    left_feed = Q(raw_data__removed_from_source_at__gte=week_ago)
    removed = Job.objects.filter(is_active=False).aggregate(
        closed=Count("pk", filter=checked_recently & Q(raw_data__source_verification__status__in=["closed", "dead"])),
        not_remote=Count("pk", filter=checked_recently & Q(raw_data__source_verification__status="not_remote")),
        left_feed=Count("pk", filter=left_feed),
        overlapping=Count("pk", filter=left_feed & checked_removed))
    removed["left_feed"] -= removed.pop("overlapping")

    stats = {
        "live": live,
        "confirmed_open": confirmed,
        "listings": listings,
        "confirmed_share": round(100 * confirmed / listings) if listings else 0,
        "organizations": totals["organizations"],
        "salary_share": round(100 * with_salary / listings) if listings else 0,
        "removed_7d": sum(removed.values()),
        "removed_closed_7d": removed["closed"],
        "removed_not_remote_7d": removed["not_remote"],
        "removed_left_feed_7d": removed["left_feed"],
        "computed_at": now,
    }
    cache.set(CACHE_KEY, stats, 60 * 60)
    return stats
