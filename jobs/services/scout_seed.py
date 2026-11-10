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


REQUEST_WORDS = {"remote", "roles", "role", "similar", "this", "one", "jobs"}
TITLE_STOP_WORDS = {"and", "the", "for", "with", "remote", "all", "genders"}


def search_terms(preferences, terms):
    """The link's own wording ("similar to this one") is not evidence about the role."""
    if not preferences.get("_seed_job_id"):
        return terms
    return [term for term in terms if term not in REQUEST_WORDS]


def _title_words(title):
    import re
    return {word for word in re.findall(r"[a-z]{3,}", (title or "").lower()) if word not in TITLE_STOP_WORDS}


def closest(preferences, jobs):
    """Order jobs by closeness to the seed: its embedding fused with its rare title words.

    Embeddings alone drift to the seed's employer and sector; title words alone miss the same
    work under another name. A word's weight falls with how many of these candidates share it,
    so "interconnection" outweighs "lead". Returns the jobs unchanged when there is no seed.
    """
    import math
    from django.db import connection
    from jobs.models import Job
    from jobs.services.scout_retrieval import reciprocal_rank_fusion

    seed_id = preferences.get("_seed_job_id")
    jobs = list({job.pk: job for job in jobs}.values())
    seed = Job.objects.filter(pk=seed_id).values("title").first() if seed_id else None
    if not seed or len(jobs) < 2:
        return jobs
    words = {job.pk: _title_words(job.title) for job in jobs}
    frequency = {}
    for found in words.values():
        for word in found:
            frequency[word] = frequency.get(word, 0) + 1
    weight = {word: math.log(1 + len(jobs) / frequency.get(word, 1)) for word in _title_words(seed["title"])}
    overlap = {pk: sum(weight[word] for word in found if word in weight) for pk, found in words.items()}
    rankings = [sorted(jobs, key=lambda job: (-overlap[job.pk], job.pk))]
    vector = embedding(preferences) if connection.vendor == "postgresql" else None
    if vector is not None:
        from pgvector.django import CosineDistance
        distance = dict(Job.objects.filter(pk__in=list(words), embedding__isnull=False)
                        .annotate(seed_distance=CosineDistance("embedding", vector)).values_list("pk", "seed_distance"))
        rankings.append(sorted(jobs, key=lambda job: (distance.get(job.pk, 2.0), job.pk)))
    return reciprocal_rank_fusion(rankings, limit=len(jobs))


def one_per_employer_first(jobs):
    """Regional copies of one opening must not fill every slot; a second role from an employer waits its turn."""
    first, later, seen = [], [], set()
    for job in jobs:
        (later if job.organization_id in seen else first).append(job)
        seen.add(job.organization_id)
    return [*first, *later]
