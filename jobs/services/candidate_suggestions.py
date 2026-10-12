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
    cache_key = f"candidate_suggestions:{job.pk}:{job.embedding_content_hash}"
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
    in_db = connection.vendor == "postgresql"
    if in_db:
        pool = (pool.annotate(distance=CosineDistance("embedding", job_vec.tolist()))
                .filter(distance__lte=1 - MIN_SIMILARITY).order_by("distance").defer("embedding")[:CANDIDATE_POOL])
    job_skills = set(job.skills or [])
    suggestions = []
    for profile in pool:
        if in_db:
            similarity = 1 - float(profile.distance)
        else:
            vec = _vector(profile.embedding)
            if vec is None:
                continue
            similarity = float(np.dot(job_vec, vec))
        shared_skills = sorted(job_skills & set(profile.skills or []))
        areas = {c.pk: c.name for c in profile.impact_areas.all()}
        shared_area = areas.get(job.category_id, "")
        # A high embedding score alone isn't a reason an employer can check.
        if similarity < MIN_SIMILARITY or not (shared_area or len(shared_skills) >= 2):
            continue
        suggestions.append(Suggestion(
            profile=profile, similarity=similarity, shared_skills=shared_skills[:6], shared_area=shared_area,
            experience_note=_experience_note(job.experience_level, profile.experience_level),
        ))
    suggestions.sort(key=lambda s: (len(s.shared_skills) + (2 if s.shared_area else 0), s.similarity), reverse=True)
    suggestions = suggestions[:limit]
    if cached_ids is None:
        cache.set(cache_key, [(s.profile.pk, s.similarity) for s in suggestions], 60 * 60)
    return suggestions
