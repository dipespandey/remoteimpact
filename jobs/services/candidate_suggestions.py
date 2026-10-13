"""Suggest people from the talent pool for an employer's paid posting.

The Job Scout matches roles to people; this runs it the other way. Only people who
are actively looking and chose to be visible (public, or "matching orgs only") are
considered, and every suggestion comes with the concrete overlap behind it so the
employer can judge it instead of trusting a score.
"""
from dataclasses import dataclass, field

import numpy as np
from django.core.cache import cache
from django.db import connection
from pgvector.django import CosineDistance

from jobs.models import SeekerProfile, TalentInvitation
from jobs.services.unified_matching_service import GENERIC_CATEGORY_SLUGS

MIN_SIMILARITY = 0.45
CANDIDATE_POOL = 80
EXPERIENCE_ORDER = ["entry", "early", "mid", "senior", "lead", "executive"]


@dataclass
class Suggestion:
    profile: SeekerProfile
    similarity: float
    shared_skills: list = field(default_factory=list)
    shared_area: str = ""
    experience_note: str = ""

    @property
    def is_public(self):
        return self.profile.visibility == SeekerProfile.Visibility.PUBLIC

    @property
    def display_name(self):
        if self.is_public:
            return self.profile.user.first_name or "Candidate"
        return "Candidate (shares details when invited)"


def _vector(value):
    if value is None:
        return None
    vec = np.asarray(value, dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm else None


def _experience_note(job_level, seeker_level):
    if not job_level or not seeker_level:
        return ""
    if job_level == seeker_level:
        return f"{seeker_level.title()}-level, as the role asks"
    if job_level in EXPERIENCE_ORDER and seeker_level in EXPERIENCE_ORDER:
        gap = EXPERIENCE_ORDER.index(seeker_level) - EXPERIENCE_ORDER.index(job_level)
        if abs(gap) == 1:
            return f"{seeker_level.title()}-level (role is {job_level})"
    return ""


def suggested_candidates(job, limit=10):
    """Best-matching visible, actively looking people for a job, with reasons."""
    job_vec = _vector(job.embedding)
    if job_vec is None:
        return []
    cache_key = _cache_key(job, limit)
    cached_ids = cache.get(cache_key)

    invited = TalentInvitation.objects.filter(job=job).values("seeker_id")
    pool = (
        SeekerProfile.objects.filter(
            wizard_completed=True,
            is_actively_looking=True,
            visibility__in=[SeekerProfile.Visibility.PUBLIC, SeekerProfile.Visibility.MATCHING],
            embedding__isnull=False,
        )
        .exclude(remote_preference=SeekerProfile.RemotePreference.ONSITE)
        .exclude(pk__in=invited)
        .exclude(user_id=job.poster_id)
        .select_related("user")
        .prefetch_related("impact_areas")
    )
    if cached_ids is not None:
        pool = pool.filter(pk__in=[pk for pk, _ in cached_ids])
    # Let Postgres rank by cosine distance and hand back only the plausible few, instead of
    # loading every profile's embedding into Python.
    from jobs.services.embedding_service import calibration
    # The cut-off follows the embedding model (MIN_SIMILARITY is the MiniLM value, used in tests).
    min_similarity = calibration()["min_candidate"] if connection.vendor == "postgresql" else MIN_SIMILARITY
    in_db = connection.vendor == "postgresql"
    if in_db:
        pool = (pool.annotate(distance=CosineDistance("embedding", job_vec.tolist()))
                .filter(distance__lte=1 - min_similarity).order_by("distance").defer("embedding")[:CANDIDATE_POOL])
    suggestions = []
    for profile in pool:
        if in_db:
            similarity = 1 - float(profile.distance)
        else:
            vec = _vector(profile.embedding)
            if vec is None:
                continue
            similarity = float(np.dot(job_vec, vec))
        suggestion = _suggestion(job, profile, similarity, min_similarity)
        if suggestion is not None:
            suggestions.append(suggestion)
    suggestions.sort(key=lambda s: (len(s.shared_skills) + (2 if s.shared_area else 0), s.similarity), reverse=True)
    suggestions = suggestions[:limit]
    if cached_ids is None:
        cache.set(cache_key, [(s.profile.pk, s.similarity) for s in suggestions], 60 * 60)
    return suggestions


def _cache_key(job, limit):
    return f"candidate_suggestions:v2:{job.pk}:{job.embedding_content_hash}:{limit}"


def _suggestion(job, profile, similarity, min_similarity):
    shared_skills = sorted(set(job.skills or []) & set(profile.skills or []))
    areas = {c.pk: c.name for c in profile.impact_areas.all()}
    shared_area = areas.get(job.category_id, "")
    if job.category and job.category.slug in GENERIC_CATEGORY_SLUGS:
        shared_area = ""
    if similarity < min_similarity or not (shared_area or len(shared_skills) >= 2):
        return None
    return Suggestion(profile=profile, similarity=similarity, shared_skills=shared_skills[:6], shared_area=shared_area,
                      experience_note=_experience_note(job.experience_level, profile.experience_level))


def suggested_candidates_for_jobs(jobs, limit=4):
    """Hydrate warm dashboard suggestions together, rechecking privacy, invitations and fit.

    Cold jobs still use PostgreSQL's vector index. Warm jobs need only their cached
    shortlist, and can share one profile query, one areas query and one invitation query.
    """
    jobs = list(jobs)
    cached = cache.get_many([_cache_key(job, limit) for job in jobs])
    results = {}
    warm = [job for job in jobs if _cache_key(job, limit) in cached]
    warm_ids = {job.pk for job in warm}
    for job in jobs:
        if job.pk not in warm_ids:
            results[job.pk] = suggested_candidates(job, limit=limit)
    ids = {pk for job in warm for pk, _ in cached[_cache_key(job, limit)]}
    if not ids:
        results.update({job.pk: [] for job in warm})
        return results
    pool = SeekerProfile.objects.filter(pk__in=ids, wizard_completed=True, is_actively_looking=True,
        visibility__in=[SeekerProfile.Visibility.PUBLIC, SeekerProfile.Visibility.MATCHING], embedding__isnull=False
        ).exclude(remote_preference=SeekerProfile.RemotePreference.ONSITE).select_related("user").prefetch_related(
            "impact_areas").defer("resume_text", "cv_facts", "search_vector")
    profiles = {profile.pk: profile for profile in pool}
    vectors = {pk: _vector(profile.embedding) for pk, profile in profiles.items()}
    invited = set(TalentInvitation.objects.filter(job_id__in=[j.pk for j in warm]).values_list("job_id", "seeker_id"))
    from jobs.services.embedding_service import calibration
    minimum = calibration()["min_candidate"] if connection.vendor == "postgresql" else MIN_SIMILARITY
    for job in warm:
        job_vec = _vector(job.embedding)
        suggestions = []
        if job_vec is not None:
            for pk, _ in cached[_cache_key(job, limit)]:
                profile = profiles.get(pk)
                vec = vectors.get(pk)
                if profile is None or vec is None or profile.user_id == job.poster_id or (job.pk, pk) in invited:
                    continue
                suggestion = _suggestion(job, profile, float(np.dot(job_vec, vec)), minimum)
                if suggestion is not None:
                    suggestions.append(suggestion)
        suggestions.sort(key=lambda s: (len(s.shared_skills) + (2 if s.shared_area else 0), s.similarity), reverse=True)
        results[job.pk] = suggestions[:limit]
    return results
