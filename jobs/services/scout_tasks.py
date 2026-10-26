"""Durable queue primitives for asynchronous Scout previews."""

import hashlib
import json
import logging
import os
import signal
import threading
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, connection, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from jobs.models import ScoutPreviewAccess, ScoutPreviewQueueGuard, ScoutPreviewTask
from jobs.services.job_service import JobService

logger = logging.getLogger(__name__)
TASK_SEMANTICS_VERSION = "scout-preview-v3"
DEFAULT_QUEUE_LIMIT = 20
DEFAULT_SESSION_LIMIT = 5
DEFAULT_SESSION_WINDOW_SECONDS = 600
DEFAULT_LEASE_SECONDS = 45
DEFAULT_HARD_TIMEOUT_SECONDS = 40
DEFAULT_RESULT_TTL_SECONDS = 180
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RETENTION_HOURS = 24


def session_digest(session_key):
    return hashlib.sha256((session_key or "").encode("utf-8")).hexdigest()


def semantic_key(intent):
    # Whitespace is normalized by the caller. Case and punctuation stay intact:
    # they can carry meaningful constraints in a natural-language brief.
    release = os.getenv("RELEASE_ID", "local")
    revision = os.getenv("SCOUT_PREVIEW_CONFIG_REVISION", "default")
    raw = f"{TASK_SEMANTICS_VERSION}\0{release}\0{revision}\0{intent}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _setting(name, default):
    return getattr(settings, name, os.getenv(name, default))


def enqueue_preview(intent, session_key, *, force_refresh=False, retry_failed=False):
    """Return (task, created); enforce per-session and global queued-work bounds."""
    digest = semantic_key(intent)
    scope = session_digest(session_key)
    now = timezone.now()
    try:
        with transaction.atomic():
            ScoutPreviewQueueGuard.objects.get_or_create(pk=1)
            if connection.features.has_select_for_update:
                ScoutPreviewQueueGuard.objects.select_for_update().get(pk=1)
            task = ScoutPreviewTask.objects.filter(semantic_key=digest).first()
            if task:
                access = ScoutPreviewAccess.objects.filter(task=task, session_digest=scope).first()
                cutoff = now - timedelta(seconds=int(_setting("SCOUT_PREVIEW_SESSION_WINDOW_SECONDS", DEFAULT_SESSION_WINDOW_SECONDS)))
                limit = int(_setting("SCOUT_PREVIEW_SESSION_LIMIT", DEFAULT_SESSION_LIMIT))
                fresh = (force_refresh and task.state == ScoutPreviewTask.State.SUCCEEDED) or (
                    task.state == ScoutPreviewTask.State.SUCCEEDED
                    and task.completed_at is not None
                    and task.completed_at < now - timedelta(seconds=int(_setting("SCOUT_PREVIEW_RESULT_TTL_SECONDS", DEFAULT_RESULT_TTL_SECONDS)))
                )
                # A failed preview is terminal for automatic page reloads and
                # polling redirects. Retrying can incur provider cost, so only
                # an explicit retry action may put it back on the queue.
                needs_refresh = (task.state == ScoutPreviewTask.State.FAILED and retry_failed) or fresh
                new_access = access is None
                recent_enqueues = ScoutPreviewAccess.objects.filter(
                    session_digest=scope, last_enqueued_at__gte=cutoff
                ).aggregate(total=Sum("enqueue_count"))["total"] or 0
                if needs_refresh and recent_enqueues >= limit:
                    raise PreviewQueueLimited("session_limit")

                if needs_refresh:
                    queue_limit = int(_setting("SCOUT_PREVIEW_QUEUE_LIMIT", DEFAULT_QUEUE_LIMIT))
                    active = ScoutPreviewTask.objects.filter(
                        state__in=[ScoutPreviewTask.State.QUEUED, ScoutPreviewTask.State.RUNNING]
                    ).count()
                    if active >= queue_limit:
                        raise PreviewQueueLimited("queue_full")

                if new_access:
                    access = ScoutPreviewAccess.objects.create(
                        task=task, session_digest=scope,
                        # Viewing an already queued or cached semantic result is
                        # free; only an actual enqueue consumes the budget.
                        enqueue_count=1 if needs_refresh else 0,
                        window_started_at=now, last_enqueued_at=now,
                    )
                if needs_refresh:
                    # Reuse the semantic row so concurrent retries stay deduplicated.
                    if not new_access:
                        if access.last_enqueued_at < cutoff:
                            access.enqueue_count = 1
                            access.window_started_at = now
                        else:
                            access.enqueue_count += 1
                        access.last_enqueued_at = now
                        access.save(update_fields=["enqueue_count", "window_started_at", "last_enqueued_at"])
                    task.state = ScoutPreviewTask.State.QUEUED
                    task.attempts = 0
                    task.result = {}
                    task.last_error = ""
                    task.completed_at = None
                    task.available_at = now
                    task.lease_until = None
                    task.lease_token = None
                    task.save(update_fields=["state", "attempts", "result", "last_error", "completed_at", "available_at", "lease_until", "lease_token", "updated_at"])
                return task, False

            window = timedelta(seconds=int(_setting("SCOUT_PREVIEW_SESSION_WINDOW_SECONDS", DEFAULT_SESSION_WINDOW_SECONDS)))
            limit = int(_setting("SCOUT_PREVIEW_SESSION_LIMIT", DEFAULT_SESSION_LIMIT))
            recent_enqueues = ScoutPreviewAccess.objects.filter(
                session_digest=scope, last_enqueued_at__gte=now - window
            ).aggregate(total=Sum("enqueue_count"))["total"] or 0
            if recent_enqueues >= limit:
                raise PreviewQueueLimited("session_limit")

            queue_limit = int(_setting("SCOUT_PREVIEW_QUEUE_LIMIT", DEFAULT_QUEUE_LIMIT))
            active = ScoutPreviewTask.objects.filter(
                state__in=[ScoutPreviewTask.State.QUEUED, ScoutPreviewTask.State.RUNNING]
            ).count()
            if active >= queue_limit:
                raise PreviewQueueLimited("queue_full")
            task = ScoutPreviewTask.objects.create(
                semantic_key=digest,
                intent_text=intent,
                available_at=now,
            )
            ScoutPreviewAccess.objects.create(task=task, session_digest=scope)
        return task, True
    except PreviewQueueLimited:
        raise
    except IntegrityError:
        # A concurrent request won the unique semantic key. Reuse its work.
        return enqueue_preview(
            intent, session_key, force_refresh=force_refresh, retry_failed=retry_failed
        )


class PreviewQueueLimited(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def accessible_task(task_id, session_key):
    try:
        return ScoutPreviewTask.objects.filter(
            pk=task_id, access_grants__session_digest=session_digest(session_key)
        ).first()
    except (ValueError, TypeError, ValidationError):
        return None


def job_fingerprint(job):
    """Fingerprint match evidence inputs so stale annotations never attach to edits."""
    raw = getattr(job, "raw_data", {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    api = raw.get("api_response") or {}
    if not isinstance(api, dict):
        api = {}
    raw_location = api.get("location") or {}
    if not isinstance(raw_location, dict):
        raw_location = {}
    organization = job.organization
    from jobs.services.scout_source_evidence import original_description
    original = original_description(raw)
    value = {
        "title": job.title,
        "application_url": job.application_url,
        "organization": [
            job.organization_id, organization.name, organization.description,
            organization.organization_type,
        ],
        "category": [job.category_id, job.category.slug if job.category else ""],
        "description": job.description,
        "requirements": job.requirements,
        "location": job.location,
        "country": job.country,
        "job_type": job.job_type,
        "experience_level": job.experience_level,
        "salary": [str(job.salary_min), str(job.salary_max), job.salary_currency],
        "skills": job.skills,
        "impact": job.impact,
        "benefits": job.benefits,
        "source_evidence": {
            "description_hash": hashlib.sha256(original.encode()).hexdigest() if original else None,
            "location": raw_location.get("name"),
            "salary_period": raw.get("salary_period"),
            "salaryPeriod": raw.get("salaryPeriod"),
            "salaryMinimum": raw.get("salaryMinimum"),
            "salaryMaximum": raw.get("salaryMaximum"),
            "salaryCurrency": raw.get("salaryCurrency"),
            "requisition_id": api.get("requisition_id"),
        },
        "updated_at": job.updated_at.isoformat() if job.updated_at else "",
        "embedding_content_hash": job.embedding_content_hash,
    }
    raw = json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _serialize_match(job):
    return {
        "job_id": int(job.pk),
        "fingerprint": job_fingerprint(job),
        "score": int(getattr(job, "scout_score", 0) or 0),
        "reasons": list(getattr(job, "scout_reasons", []) or [])[:12],
        "needs_confirmation": bool(getattr(job, "scout_needs_confirmation", False)),
        "close_match": bool(getattr(job, "scout_close_match", False)),
        "caveats": list(getattr(job, "scout_caveats", []) or [])[:12],
    }


def _compact_diagnostics(diagnostics):
    result = {}
    for key, value in diagnostics.items():
        name = str(key)[:64]
        if any(part in name.lower() for part in ("prompt", "response", "body", "text", "ids")):
            continue
        if isinstance(value, (bool, int, float)):
            result[name] = value
        elif isinstance(value, str) and len(value) <= 120:
            result[name] = value
        elif isinstance(value, dict):
            entries = {}
            for subkey, item in list(value.items())[:24]:
                if isinstance(item, (bool, int, float)):
                    entries[str(subkey)[:64]] = item
                elif isinstance(item, str) and len(item) <= 80:
                    entries[str(subkey)[:64]] = item
            if entries:
                result[name] = entries
        elif isinstance(value, list) and name == "stages":
            stages = []
            for item in value[:20]:
                if not isinstance(item, dict):
                    continue
                stage = {
                    str(key)[:48]: val for key, val in item.items()
                    if str(key).lower() in {"stage", "status", "seconds", "count", "retry"}
                    and isinstance(val, (str, int, float, bool))
                }
                if stage:
                    stages.append(stage)
            result[name] = stages
    if isinstance(result.get("hard_rejections"), dict):
        result["hard_rejections"] = {
            str(key)[:48]: int(value) for key, value in list(result["hard_rejections"].items())[:16]
            if isinstance(value, (int, float))
        }
    return result


def compute_task(task_id):
    """Run matching and persist only JSON-safe, bounded display data."""
    task = ScoutPreviewTask.objects.get(pk=task_id)
    diagnostics = {}
    from jobs.services.job_scout_service import JobScoutService

    preferences, matches = JobScoutService.search_intent(
        task.intent_text,
        diagnostics=diagnostics,
        since=timezone.now() - timedelta(days=30),
        limit=3,
    )
    rows = [_serialize_match(job) for job in matches[:3]]
    older_rows = []
    if not matches and not diagnostics.get("review_incomplete"):
        from jobs.services.scout_alternatives import older_openings

        for job in older_openings(preferences)[:3]:
            older_rows.append({"job_id": int(job.pk), "fingerprint": job_fingerprint(job), "evidence": str(getattr(job, "alternative_evidence", ""))[:1200]})
    safe_preferences = {key: value for key, value in preferences.items() if not key.startswith("_")}
    payload = {
        "preferences": safe_preferences,
        "matches": rows,
        "older_jobs": older_rows,
        "diagnostics": _compact_diagnostics(diagnostics),
    }
    encoded = json.dumps(payload, cls=DjangoJSONEncoder, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 64_000:
        raise ValueError("Scout preview result exceeded the storage limit")
    return json.loads(encoded)


def claim_task():
    """Lease one ready task and return (id, token), recovering expired leases."""
    now = timezone.now()
    max_attempts = int(_setting("SCOUT_PREVIEW_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS))
    with transaction.atomic():
        expired = ScoutPreviewTask.objects.filter(state=ScoutPreviewTask.State.RUNNING, lease_until__lt=now)
        expired.filter(attempts__gte=max_attempts).update(
            state=ScoutPreviewTask.State.FAILED,
            last_error="Worker lease expired",
            completed_at=now,
            lease_until=None,
            lease_token=None,
        )
        expired.filter(attempts__lt=max_attempts).update(
            state=ScoutPreviewTask.State.QUEUED,
            available_at=now,
            lease_until=None,
            lease_token=None,
            last_error="",
        )
        query = ScoutPreviewTask.objects.filter(
            state=ScoutPreviewTask.State.QUEUED,
            available_at__lte=now,
            attempts__lt=max_attempts,
        ).order_by("created_at", "id")
        if transaction.get_connection().features.has_select_for_update_skip_locked:
            query = query.select_for_update(skip_locked=True)
        else:
            query = query.select_for_update()
        task = query.first()
        if not task:
            return None
        task.state = ScoutPreviewTask.State.RUNNING
        task.attempts += 1
        task.lease_until = now + timedelta(seconds=int(_setting("SCOUT_PREVIEW_LEASE_SECONDS", DEFAULT_LEASE_SECONDS)))
        task.lease_token = uuid.uuid4()
        task.save(update_fields=["state", "attempts", "lease_until", "lease_token", "updated_at"])
        return task.pk, task.lease_token


def _deadline_handler(_signum, _frame):
    raise TimeoutError("Scout preview worker deadline exceeded")


def _compute_with_deadline(task_id):
    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        return compute_task(task_id)
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    signal.signal(signal.SIGALRM, _deadline_handler)
    lease_seconds = int(_setting("SCOUT_PREVIEW_LEASE_SECONDS", DEFAULT_LEASE_SECONDS))
    hard_timeout = min(
        int(_setting("SCOUT_PREVIEW_HARD_TIMEOUT_SECONDS", DEFAULT_HARD_TIMEOUT_SECONDS)),
        max(1, lease_seconds - 5),
    )
    signal.setitimer(signal.ITIMER_REAL, hard_timeout)
    try:
        return compute_task(task_id)
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def execute_claimed_task(task_id, lease_token):
    """Run a claimed unit and persist success/failure while respecting its lease."""
    try:
        result = _compute_with_deadline(task_id)
    except Exception as exc:  # noqa: BLE001 - worker must keep processing later tasks
        logger.warning("Scout preview computation failed (%s)", type(exc).__name__)
        with transaction.atomic():
            task = ScoutPreviewTask.objects.select_for_update().filter(
                pk=task_id, state=ScoutPreviewTask.State.RUNNING, lease_token=lease_token
            ).first()
            if task:
                retry = task.attempts < int(_setting("SCOUT_PREVIEW_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS))
                task.state = ScoutPreviewTask.State.QUEUED if retry else ScoutPreviewTask.State.FAILED
                task.available_at = timezone.now() + timedelta(seconds=min(30 * task.attempts, 120))
                task.lease_until = None
                task.lease_token = None
                task.last_error = type(exc).__name__[:240]
                task.completed_at = None if retry else timezone.now()
                task.save(update_fields=["state", "available_at", "lease_until", "lease_token", "last_error", "completed_at", "updated_at"])
        return False

    with transaction.atomic():
        task = ScoutPreviewTask.objects.select_for_update().filter(
            pk=task_id, state=ScoutPreviewTask.State.RUNNING, lease_token=lease_token
        ).first()
        if not task:
            return False
        incomplete_empty = not result.get('matches') and result.get('diagnostics', {}).get('review_incomplete') is True
        if incomplete_empty:
            max_attempts = int(_setting("SCOUT_PREVIEW_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS))
            if task.attempts < max_attempts:
                # A completed worker call is not a completed relevance review.
                # Retry only empty failures; do not delay useful partial results
                # or retry a genuine inventory no-match. Existing leases/caps win.
                task.state = ScoutPreviewTask.State.QUEUED
                task.result = result
                task.available_at = timezone.now() + timedelta(seconds=3)
                task.lease_until = None
                task.lease_token = None
                task.last_error = 'ReviewIncomplete'
                task.completed_at = None
                task.save(update_fields=['state', 'result', 'available_at', 'lease_until', 'lease_token', 'last_error', 'completed_at', 'updated_at'])
                return False
            result = {**result, 'diagnostics': {**result.get('diagnostics', {}), 'review_retry_exhausted': True}}
        task.state = ScoutPreviewTask.State.SUCCEEDED
        task.result = result
        task.lease_until = None
        task.lease_token = None
        task.last_error = ""
        task.completed_at = timezone.now()
        task.save(update_fields=["state", "result", "lease_until", "lease_token", "last_error", "completed_at", "updated_at"])
    return True


def hydrate_result(result):
    """Recheck listing availability and restore only the stored display annotations."""
    match_rows = result.get("matches", []) if isinstance(result, dict) else []
    ids = [row.get("job_id") for row in match_rows if isinstance(row, dict) and isinstance(row.get("job_id"), int)]
    available = JobService.get_available_jobs().filter(pk__in=ids).select_related("organization", "category")
    by_id = {job.pk: job for job in available}
    jobs = []
    stale = False
    for row in match_rows:
        job = by_id.get(row.get("job_id"))
        if job is None:
            stale = True
            continue
        if row.get("fingerprint") != job_fingerprint(job):
            stale = True
            continue
        job.scout_score = row.get("score", 0)
        job.scout_reasons = row.get("reasons", [])
        job.scout_needs_confirmation = bool(row.get("needs_confirmation"))
        job.scout_close_match = bool(row.get("close_match"))
        job.scout_caveats = row.get("caveats", [])
        jobs.append(job)
    older_rows = result.get("older_jobs", []) if isinstance(result, dict) else []
    older_ids = [row.get("job_id") for row in older_rows if isinstance(row, dict)]
    older_available = JobService.get_available_jobs().filter(pk__in=older_ids).select_related("organization", "category")
    older_by_id = {job.pk: job for job in older_available}
    older_jobs = []
    for row in older_rows:
        job = older_by_id.get(row.get("job_id"))
        if job is None:
            stale = True
            continue
        if row.get("fingerprint") != job_fingerprint(job):
            stale = True
            continue
        job.alternative_evidence = row.get("evidence", "")
        older_jobs.append(job)
    return jobs, older_jobs, stale


def prune_tasks():
    cutoff = timezone.now() - timedelta(hours=int(_setting("SCOUT_PREVIEW_RETENTION_HOURS", DEFAULT_RETENTION_HOURS)))
    return ScoutPreviewTask.objects.filter(
        Q(state__in=[ScoutPreviewTask.State.SUCCEEDED, ScoutPreviewTask.State.FAILED], completed_at__lt=cutoff)
        | Q(state=ScoutPreviewTask.State.QUEUED, updated_at__lt=cutoff)
    ).delete()[0]
