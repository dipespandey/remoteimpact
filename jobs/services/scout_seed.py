"""Similar-roles searches start from a live job instead of retyped title text.

The seed sets three things the short request cannot: which opening (and its
same-employer reposts) to leave out, a stored embedding for semantic retrieval,
and a fuller brief for the review model. The deterministic rules keep reading
the short request the person saw.
"""
import hashlib

from django.utils.html import strip_tags

BRIEF_DESCRIPTION_CHARS = 700


def load(seed_job_id):
    if not seed_job_id:
        return None
    from jobs.services.job_service import JobService
    return JobService.get_available_jobs().select_related("organization", "category").filter(pk=seed_job_id).first()


def preferences(intent_text, seed, parsed):
    """Extend the rule-based reading of the request; no interpretation or planning call is needed."""
    title = " ".join((seed.title or "").split())
    description = " ".join(strip_tags(seed.description or "").split())[:BRIEF_DESCRIPTION_CHARS]
    lines = [f"Request: {intent_text}",
             "Find openings that do the same kind of work as this one, at a similar level.",
             f"Title: {title}", f"Organization: {seed.organization.name}"]
    if seed.category:
        lines.append(f"Field: {seed.category.name}")
    if seed.experience_level:
        lines.append(f"Level: {seed.get_experience_level_display()}")
    if description:
        lines.append(f"About the role: {description}")
    return {**parsed,
            "_seed_job_id": seed.pk,
            "_review_brief": "\n".join(lines),
            "_retrieval_terms": [title.lower()][:1] if 2 <= len(title) <= 80 else [],
            "_retrieval_brief_hash": hashlib.sha256(parsed["intent_text"].encode()).hexdigest()}


def review_request(preferences):
    return (preferences.get("_review_brief") or preferences["intent_text"])[:2000]


def exclude_seed(qs, preferences):
    seed_id = preferences.get("_seed_job_id")
    if not seed_id:
        return qs
    from jobs.models import Job
    seed = Job.objects.filter(pk=seed_id).values("organization_id", "title").first()
    qs = qs.exclude(pk=seed_id)
    return qs.exclude(organization_id=seed["organization_id"], title__iexact=seed["title"]) if seed else qs


def embedding(preferences):
    seed_id = preferences.get("_seed_job_id")
    if not seed_id:
        return None
    from jobs.models import Job
    return Job.objects.filter(pk=seed_id).values_list("embedding", flat=True).first()
