"""Log what people were shown and what they did, for measuring and later learning ranking.

The browser reports impressions and clicks (so cached pages and live filter updates are covered);
the server logs applies and saves. Every function swallows errors: logging must never break a page.
"""
import logging
import re

from jobs.services.job_stats import is_probably_bot, visitor_key

logger = logging.getLogger(__name__)
RETENTION_DAYS = 120
SURFACES = {"search", "category", "home", "scout", "suggestions"}
KEY = re.compile(r"^[a-z0-9]{8,32}$")


def _base(request, kind, surface=""):
    user = getattr(request, "user", None)
    return {"kind": kind, "surface": surface if surface in SURFACES else "", "visitor": visitor_key(request),
            "user_id": user.pk if user is not None and user.is_authenticated else None}


def log_impression(request, key, surface, job_ids, query=""):
    from jobs.models import RankingEvent

    try:
        ids = [int(i) for i in job_ids][:100]
        if not ids or not KEY.match(key or "") or is_probably_bot(request):
            return
        RankingEvent.objects.create(**_base(request, RankingEvent.Kind.IMPRESSION, surface), impression_key=key,
                                    query=str(query or "")[:200], job_ids=ids)
    except Exception:  # noqa: BLE001
        logger.exception("Could not log impression")


def log_click(request, key, job_id, position):
    """Only counted when this visitor really was shown that job in that list."""
    from jobs.models import RankingEvent

    try:
        if not KEY.match(key or "") or is_probably_bot(request):
            return
        shown = RankingEvent.objects.filter(kind=RankingEvent.Kind.IMPRESSION, impression_key=key,
                                            visitor=visitor_key(request)).first()
        if shown is None or job_id not in shown.job_ids:
            return
        RankingEvent.objects.create(**_base(request, RankingEvent.Kind.CLICK, shown.surface), impression_key=key,
                                    job_id=job_id, position=position)
    except Exception:  # noqa: BLE001
        logger.exception("Could not log click")


def log_outcome(request, kind, job_id):
    """An apply or save; joined to what was shown by visitor and job."""
    from jobs.models import RankingEvent

    try:
        if not is_probably_bot(request):
            RankingEvent.objects.create(**_base(request, kind), job_id=job_id)
    except Exception:  # noqa: BLE001
        logger.exception("Could not log %s", kind)


def purge_old():
    from datetime import timedelta

    from django.utils import timezone

    from jobs.models import RankingEvent

    return RankingEvent.objects.filter(created_at__lt=timezone.now() - timedelta(days=RETENTION_DAYS)).delete()[0]
