"""Restore a Scout's recorded initial result without running matching on GET."""
from jobs.models import ConsumerFunnelEvent
from jobs.services.job_service import JobService

INITIAL_OUTCOMES = (
    ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY,
    ConsumerFunnelEvent.Event.INITIAL_EMAIL_SENT,
    ConsumerFunnelEvent.Event.INITIAL_EMAIL_FAILED,
)


def snapshot_matches(matches):
    return [
        {
            "job_id": job.pk,
            "score": int(getattr(job, "scout_score", 0)),
            "reasons": list(getattr(job, "scout_reasons", []) or []),
            "needs_confirmation": bool(getattr(job, "scout_needs_confirmation", False)),
            "close_match": bool(getattr(job, "scout_close_match", False)),
            "caveats": list(getattr(job, "scout_caveats", []) or []),
        }
        for job in matches
    ]


def initial_result_context(alert):
    """Caller must first scope alert to the authenticated owner."""
    event = alert.funnel_events.filter(event__in=INITIAL_OUTCOMES).order_by("-created_at", "-pk").first()
    metadata = (event.metadata or {}) if event else {}
    diagnostics = metadata.get("matching") or {}
    incomplete = bool(diagnostics.get("review_incomplete") or diagnostics.get("empty_reason") == "review_incomplete")
    context = {
        "preview_jobs": [],
        "initial_result_state": "unavailable",
        "initial_email_failed": bool(event and event.event == ConsumerFunnelEvent.Event.INITIAL_EMAIL_FAILED),
        "initial_review_incomplete": incomplete,
    }
    rows = metadata.get("results")
    has_snapshot = metadata.get("results_version") == 1 and isinstance(rows, list)
    deliveries_query = alert.deliveries.select_related("job__organization", "job__category")
    if has_snapshot:
        deliveries_query = deliveries_query.filter(job_id__in=[row["job_id"] for row in rows])
    deliveries = list(deliveries_query.order_by("-sent_at", "-pk")[:10])
    delivery_by_job = {delivery.job_id: delivery for delivery in deliveries}
    if has_snapshot:
        jobs = JobService.get_available_jobs().filter(pk__in=[row["job_id"] for row in rows]).select_related("organization", "category")
        by_id = {job.pk: job for job in jobs}
        for row in rows:
            job = by_id.get(row["job_id"])
            if job is None:
                continue
            job.scout_score = row["score"]
            job.scout_reasons = row["reasons"]
            job.scout_needs_confirmation = row["needs_confirmation"]
            job.scout_close_match = row["close_match"]
            job.scout_caveats = row["caveats"]
            context["preview_jobs"].append(job)
        if rows:
            context["initial_result_state"] = "matches" if context["preview_jobs"] else "expired"
            context["initial_results_changed"] = len(context["preview_jobs"]) < len(rows)
        elif event.event == ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY:
            context["initial_result_state"] = "empty"
    elif event and event.event == ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY:
        # Legacy empty outcomes are still known; lack of a snapshot is not a
        # reason to spend another matching request or change that outcome.
        context["initial_result_state"] = "empty"
    elif deliveries:
        # Older successful sends can be restored from delivery records.
        available_ids = set(JobService.get_available_jobs().filter(pk__in=delivery_by_job).values_list("pk", flat=True))
        for delivery in deliveries:
            if delivery.job_id in available_ids:
                job = delivery.job
                job.scout_score = delivery.match_score
                job.scout_reasons = delivery.match_reasons
                context["preview_jobs"].append(job)
        context["initial_result_state"] = "matches" if context["preview_jobs"] else "expired"
        context["initial_results_changed"] = len(context["preview_jobs"]) < len(deliveries)
    if context['preview_jobs']:
        # Validate stored suggestions against current source facts without
        # matching again or altering the historical email/delivery ledger.
        from jobs.services.job_scout_service import JobScoutService
        from jobs.services.scout_verification import verify_matches
        previous_count = len(context['preview_jobs'])
        context['preview_jobs'] = verify_matches(context['preview_jobs'],
            JobScoutService.preferences_for_alert(alert), {}, annotate=False)
        if len(context['preview_jobs']) < previous_count:
            context['initial_results_changed'] = True
            context['initial_results_rechecked'] = True
            context['initial_result_state'] = 'matches' if context['preview_jobs'] else 'unavailable'
    for job in context["preview_jobs"]:
        delivery = delivery_by_job.get(job.pk)
        if delivery:
            job.scout_delivery_id = delivery.pk
            job.scout_feedback = delivery.feedback
    rejections = diagnostics.get("hard_rejections") or {}
    context["location_blocked"] = bool(
        context["initial_result_state"] == "empty" and not incomplete and rejections
        and max(rejections, key=rejections.get) in ("location", "location_rejected", "excluded_location")
    )
    return context
