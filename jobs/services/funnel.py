from jobs.models import ConsumerFunnelEvent, EmployerFunnelEvent


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
            obj, _ = ConsumerFunnelEvent.objects.get_or_create(
                dedupe_key=dedupe_key, defaults=values
            )
            return obj
        return ConsumerFunnelEvent.objects.create(**values)
    except Exception:
        return None
