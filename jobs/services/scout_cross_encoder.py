"""Small CPU cross-encoder that picks which retrieved openings the AI reviewer judges."""
import logging
import threading

from django.conf import settings

from jobs.services.scout_constraints import plain_job_field

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_model = None
_unavailable = False
_allowed = False
MIN_SECONDS_LEFT = 10


def allow_in_this_process():
    """About 150 MB while scoring, so only the preview worker loads it, on first use; other searches keep retrieval order."""
    global _allowed
    _allowed = True


def get_model():
    global _model, _unavailable
    name = getattr(settings, "SCOUT_CROSS_ENCODER", "")
    if not _allowed or not name or _unavailable or getattr(settings, "TESTING", False):
        return None
    with _lock:
        if _model is None:
            try:
                from sentence_transformers import CrossEncoder
                _model = CrossEncoder(name, device="cpu", max_length=256)
            except Exception:  # noqa: BLE001 - review falls back to retrieval order
                _unavailable = True
                logger.warning("Scout cross-encoder unavailable; using retrieval order", exc_info=True)
                return None
    return _model


def job_text(job):
    organization = job.organization.name if job.organization_id else ""
    category = job.category.name if job.category_id else ""
    duties = " ".join(f"{plain_job_field(job, 'description')} {plain_job_field(job, 'requirements')}".split())
    return f"{plain_job_field(job, 'title')}. {organization}. {category}. {job.location or ''}. {duties[:1200]}"


def order(query, jobs):
    """Most relevant first, or None when the model is off or fails."""
    from jobs.services.scout_workflow import seconds_left
    # The AI review needs the rest of the search budget; ranking is optional.
    if not jobs or not query or seconds_left() < MIN_SECONDS_LEFT:
        return None
    model = get_model()
    if model is None:
        return None
    try:
        import torch
        with torch.inference_mode():
            scores = model.predict([(query[:1000], job_text(job)) for job in jobs], batch_size=8, show_progress_bar=False)
    except Exception:  # noqa: BLE001
        logger.warning("Scout cross-encoder scoring failed; using retrieval order", exc_info=True)
        return None
    ranked = sorted(zip(scores, range(len(jobs))), key=lambda pair: -float(pair[0]))
    return [jobs[index] for _, index in ranked]
