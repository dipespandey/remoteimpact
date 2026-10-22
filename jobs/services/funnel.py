from jobs.models import ConsumerFunnelEvent, EmployerFunnelEvent
from django.db import transaction


def record_employer_page_request(request, *, page, job=None):
    """Deduplicated requests, never proof of a human or of employer intent.

    Browser-shaped link scanners can pass the UA check. Keep that uncertainty
    explicit instead of promoting outreach query parameters to intent evidence.
    """
    from django.core.cache import cache
    import hashlib
    from urllib.parse import urlsplit
    from jobs.services import job_stats

    if request.method != "GET" or job_stats.is_probably_bot(request):
        return
    if getattr(request.user, "is_staff", False):
        return
    if page == "feature" and not (
        request.GET.get("source") in ("outreach", "employer_report", "employers")
        or job_stats.viewed_today(job.pk, request)
    ):
        return
    # Anonymous sessions may be created during the first response. A coarse
    # daily fingerprint stays stable across that boundary; no raw IP is stored.
    if getattr(request.user, "is_authenticated", False):
        visitor = f"user:{request.user.pk}"
    else:
        raw = (request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR", ""))
        raw += request.META.get("HTTP_USER_AGENT", "")
        visitor = hashlib.sha256(raw.encode()).hexdigest()[:20]
    key = f"employer-page:v2:{page}:{getattr(job, 'pk', 0)}:{visitor}"
    try:
        if not cache.add(key, 1, 24 * 60 * 60):
            return
    except Exception:
        return  # A cache outage must not inflate request counts.
    try:
        host = urlsplit(request.META.get("HTTP_REFERER", "")).hostname or ""
    except ValueError:
        host = ""
    signal = "email_link_protection" if host == "link.edgepilot.com" else "unverified_request"
    event = (EmployerFunnelEvent.Event.FEATURE_PAGE_REQUESTED if page == "feature"
             else EmployerFunnelEvent.Event.POST_JOB_VIEWED)
    return record_employer_event(event, user=request.user, job=job, metadata={
        "page": page, "source": request.GET.get("source", "")[:80],
        "method": "GET", "signal": signal, "tracking_version": 2,
    })


def record_employer_event(event, *, user=None, job=None, metadata=None):
    """Record a coarse conversion event without storing IPs or page contents."""
    try:
        return EmployerFunnelEvent.objects.create(
            event=event, user=user if getattr(user, "is_authenticated", False) else None,
            job=job, metadata=metadata or {},
        )
    except Exception:
        return None


def record_consumer_event(
    event, *, user=None, alert=None, metadata=None, dedupe_key=None
):
    """Record a conversion event without allowing analytics to break the flow."""
    values = {
        "event": event,
        "user": user if getattr(user, "is_authenticated", False) else None,
        "alert": alert,
        "metadata": metadata or {},
    }
    try:
        if dedupe_key:
            with transaction.atomic():
                obj, _ = ConsumerFunnelEvent.objects.select_for_update().get_or_create(
                    dedupe_key=dedupe_key, defaults=values
                )
                # Checkout and subscription webhooks can arrive in either
                # order. Definitive trial evidence must enrich an existing
                # start event rather than being lost to deduplication. Keep
                # this monotonic: a later active state is not a cash receipt
                # and must not rewrite a trial start as a paid conversion.
                if (event == ConsumerFunnelEvent.Event.PAYMENT_COMPLETED
                        and obj.event == event and obj.user_id == getattr(values['user'], 'pk', None)
                        and values['metadata'].get('trial') is True):
                    previous = obj.metadata if isinstance(obj.metadata, dict) else {}
                    if previous.get('trial') is not True:
                        obj.metadata = {**previous, 'trial': True}
                        obj.save(update_fields=['metadata'])
            return obj
        return ConsumerFunnelEvent.objects.create(**values)
    except Exception:
        return None
