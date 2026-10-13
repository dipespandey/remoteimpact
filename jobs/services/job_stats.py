"""Per-job engagement counters for employer reporting."""
import re
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

BOT_UA = re.compile(
    r"bot|crawl|spider|slurp|preview|fetch|monitor|headless|python|curl|wget|httpclient|"
    r"java/|go-http|okhttp|axios|node-fetch|scrapy|facebookexternalhit|embedly|whatsapp|"
    r"telegram|slack|discord|lighthouse|pagespeed",
    re.I,
)
FIELDS = {"views", "apply_clicks", "scout_deliveries"}


def is_probably_bot(request):
    agent = request.META.get("HTTP_USER_AGENT", "")
    return not agent.startswith("Mozilla") or bool(BOT_UA.search(agent))


def visitor_key(request):
    """A coarse, non-identifying key used only to de-duplicate counts for a day."""
    import hashlib

    if getattr(request, "session", None) is not None and request.session.session_key:
        raw = request.session.session_key
    else:
        raw = (request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR", "")) + request.META.get(
            "HTTP_USER_AGENT", ""
        )
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def viewed_today(job_id, request):
    """True when this visitor's page-view beacon for the job was counted today.

    The apply link only appears on job pages, which send the beacon first, so a
    click without a view is a crawler following the link directly.
    """
    key = f"jobstat:views:{job_id}:{timezone.localdate().isoformat()}:{visitor_key(request)}"
    try:
        return cache.get(key) is not None
    except Exception:  # noqa: BLE001 - if the cache is down, keep counting
        return True


def record(job_id, field, *, amount=1, request=None, dedupe=True):
    """Increment today's counter. With a request, one count per visitor per job per day."""
    if field not in FIELDS:
        raise ValueError(field)
    today = timezone.localdate()
    if request is not None and dedupe:
        key = f"jobstat:{field}:{job_id}:{today.isoformat()}:{visitor_key(request)}"
        try:
            if not cache.add(key, 1, 60 * 60 * 26):
                return False
        except Exception:  # noqa: BLE001 - counting must never break a page
            pass
    if request is not None and field in ("views", "apply_clicks"):
        from jobs.services import country_activity
        country_activity.record(request, field)
    from jobs.models import JobDailyStat

    updated = JobDailyStat.objects.filter(job_id=job_id, date=today).update(**{field: F(field) + amount})
    if not updated:
        try:
            with transaction.atomic():
                JobDailyStat.objects.create(job_id=job_id, date=today, **{field: amount})
        except IntegrityError:
            JobDailyStat.objects.filter(job_id=job_id, date=today).update(**{field: F(field) + amount})
    return True


def totals(job_ids, days=30):
    """{job_id: {"views": n, "apply_clicks": n, "scout_deliveries": n}} over the window."""
    from jobs.models import JobDailyStat

    since = timezone.localdate() - timedelta(days=days - 1)
    rows = (
        JobDailyStat.objects.filter(job_id__in=list(job_ids), date__gte=since)
        .values("job_id")
        .annotate(views=Sum("views"), apply_clicks=Sum("apply_clicks"), scout_deliveries=Sum("scout_deliveries"))
    )
    return {row["job_id"]: row for row in rows}


def with_referral(url):
    """Credit Remote Impact on apply links while preserving destination payloads."""
    from jobs.services.outbound_links import with_outbound_attribution

    return with_outbound_attribution(url, medium="job_board", campaign="remote_impact_jobs")
