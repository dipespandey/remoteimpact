"""
Unified Matching Service for Remote Impact.

Single source of truth for all job-seeker matching, combining:
- Semantic similarity (40%) - calibrated embedding similarity
- Profile matching (35%) - skills, experience, preferences
- Impact fit (15%) - cause alignment + role/org impact potential
- Lexical matching (10%) - exact term bonus from PostgreSQL full-text search

Pipeline:
1. RETRIEVE: HNSW vector search for top 150 candidates (fast, high recall)
2. SCORE: Re-rank with all 4 scoring components
3. RETURN: Top 25 with detailed breakdown
"""

import math
import re
from dataclasses import dataclass
from typing import Optional

from django.core.cache import cache
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Q, Case, When, IntegerField
from pgvector.django import CosineDistance

from jobs.models import Job, SeekerProfile
from jobs.services.embedding_service import embed_job, embed_seeker
from jobs.services.impact_potential_service import ImpactPotentialService
from jobs.services.job_service import JobService
from jobs.constants.skills import SKILLS_BY_SLUG


# Scoring weights (must sum to 1.0)
WEIGHTS = {
    'semantic': 0.40,       # Embedding similarity - backbone of matching
    'lexical': 0.10,        # Full-text search - exact term bonus
    'profile': 0.35,        # Skills, experience, preferences
    'impact': 0.15,         # Cause alignment + role/org impact potential
}

# all-MiniLM cosine similarities for short profile-vs-JD text are compressed.
# Production top-k similarities cluster around 0.52-0.67, with rare excellent
# matches above that. Calibrate against that range so semantic scoring helps
# ranking without making every plausible match look perfect.
# MiniLM values, kept for reference; the live cut-offs come from embedding_service.calibration().
SEMANTIC_SIMILARITY_FLOOR = 0.50
SEMANTIC_SIMILARITY_CEILING = 0.68

# Work style keywords mapping
WORK_STYLE_KEYWORDS = {
    "builder": [
        "engineer", "developer", "software", "product", "design", "build", "create",
        "architect", "technical", "code", "programming", "frontend", "backend",
        "fullstack", "devops", "infrastructure", "platform", "ux", "ui",
    ],
    "strategist": [
        "strategy", "communications", "marketing", "policy", "advocacy", "campaigns",
        "partnerships", "business", "development", "growth", "brand", "content",
        "storytelling", "media", "pr", "public", "relations", "outreach",
    ],
    "operator": [
        "operations", "ops", "finance", "hr", "human", "resources", "admin",
        "administrative", "logistics", "procurement", "legal", "compliance",
        "accounting", "payroll", "office", "facilities", "coordinator",
    ],
    "direct": [
        "program", "project", "field", "service", "delivery", "implementation",
        "community", "outreach", "engagement", "training", "facilitation",
        "volunteer", "case", "management", "social", "worker",
    ],
    "researcher": [
        "research", "analyst", "analysis", "data", "scientist", "quantitative",
        "qualitative", "evaluation", "assessment", "study", "survey", "statistics",
        "modeling", "insights", "intelligence", "academic",
    ],
}

# Experience level compatibility
EXPERIENCE_COMPATIBILITY = {
    "early": {"entry": 100, "mid": 65, "senior": 30, "leadership": 15},
    "mid": {"entry": 70, "mid": 100, "senior": 75, "leadership": 45},
    "senior": {"entry": 45, "mid": 75, "senior": 100, "leadership": 82},
    "leadership": {"entry": 25, "mid": 55, "senior": 86, "leadership": 100},
    "career_changer": {"entry": 95, "mid": 75, "senior": 35, "leadership": 20},
}

LEVEL_KEYWORDS = {
    "entry": ["entry", "junior", "graduate", "intern", "trainee", "early career"],
    "mid": ["mid-level", "mid level", "intermediate", "associate", "2-5 years", "3-5 years"],
    "senior": ["senior", "lead", "staff", "principal", "sr.", "sr ", "5+ years", "7+ years"],
    "leadership": ["director", "head of", "vp", "chief", "manager", "cto", "ceo", "coo", "executive"],
}

JOB_LEVEL_MAP = {
    "entry": "entry",
    "internship": "entry",
    "mid": "mid",
    "senior": "senior",
    "executive": "leadership",
    "leadership": "leadership",
}

WORK_STYLE_LABELS = {
    "builder": "Builder",
    "strategist": "Strategist",
    "operator": "Operator",
    "direct": "Direct service",
    "researcher": "Researcher",
}


@dataclass
class MatchResult:
    """Structured result for a single job match."""
    job: Job
    score: float               # Final weighted score (0-100)
    semantic_score: float      # Embedding similarity (0-100)
    lexical_score: float       # FTS rank (0-100)
    profile_score: float       # Profile match (0-100)
    impact_score: float        # Impact potential (0-100)
    impact_tier: str           # "exceptional", "high", "moderate", "standard"
    reasons: list              # Human-readable match reasons
    gaps: list                 # Skill gaps
    impact_reasons: list       # Impact-specific reasons


@dataclass
class CandidateMatchResult:
    """Structured result for an employer's candidate match."""
    seeker: SeekerProfile
    job: Job
    score: float
    semantic_score: float
    lexical_score: float
    profile_score: float
    impact_score: float
    impact_tier: str
    reasons: list
    gaps: list
    impact_reasons: list


class UnifiedMatchingService:
    """Single source of truth for job-seeker matching."""

    # Retrieval parameters
    CANDIDATE_LIMIT = 150      # How many to retrieve from HNSW
    FINAL_LIMIT = 25           # How many to return after re-ranking
    MATCH_CACHE_TTL = 15 * 60  # User-facing matches can be a few minutes stale.

    @classmethod
    def order_jobs_for_seeker(cls, queryset, seeker: SeekerProfile, keep_all: bool = False):
        """Show a fresh chronological feed from a pool of relevant jobs.

        With ``keep_all`` (explicit filters), every filtered job stays and the seeker's
        matches come first; otherwise only the match pool is shown.
        """
        matches = cls.get_matches(seeker, limit=100)
        ranked_ids = [result.job.id for result in matches]
        if not ranked_ids:
            return queryset.order_by("-posted_at", "-id")
        if keep_all:
            from django.db.models import Case, IntegerField, Value, When
            return queryset.annotate(
                _seeker_match=Case(When(pk__in=ranked_ids, then=Value(0)), default=Value(1), output_field=IntegerField())
            ).order_by("_seeker_match", "-posted_at", "-id")

        # "Best for you" means best first: order by match rank. (The pool is already limited to open,
        # recently checked roles, so stale listings can't float to the top.)
        from django.db.models import Case, IntegerField, Value, When
        rank = Case(*[When(pk=pk, then=Value(i)) for i, pk in enumerate(ranked_ids)], output_field=IntegerField())
        return queryset.filter(pk__in=ranked_ids).annotate(_match_rank=rank).order_by("_match_rank", "-posted_at")

    @classmethod
    def get_matches(
        cls,
        seeker: SeekerProfile,
        limit: int = 25,
    ) -> list[MatchResult]:
        """
        Get top job matches for a seeker using unified 4-component scoring.

        Pipeline:
        1. RETRIEVE: Vector search for top candidates
        2. SCORE: Re-rank with semantic + lexical + profile + impact
        3. RETURN: Top N with detailed breakdown
        """
        cache_key = cls._matches_cache_key(seeker, limit)
        cached = cache.get(cache_key)
        if cached:
            hydrated = cls._hydrate_cached_matches(cached)
            if hydrated:
                return hydrated

        # Stage 1: Retrieve candidates via vector search
        candidates = cls._retrieve_candidates(seeker, limit=cls.CANDIDATE_LIMIT)

        if not candidates:
            return []

        match_context = cls._build_match_context(seeker)
        lexical_scores = cls._bulk_lexical_scores(
            [job.id for job, _ in candidates],
            match_context["search_terms"],
        )

        # Stage 2: Score each candidate with all components
        results = []
        for job, semantic_score in candidates:
            result = cls._score_candidate(
                seeker,
                job,
                semantic_score,
                lexical_score=lexical_scores.get(job.id, 50.0),
                match_context=match_context,
            )
            results.append(result)

        # Stage 3: Sort by final score and return top N
        results.sort(key=lambda r: r.score, reverse=True)
        results = results[:limit]
        cache.set(cache_key, [cls._serialize_match(result) for result in results], cls.MATCH_CACHE_TTL)
        return results

    @classmethod
    def get_candidate_matches(
        cls,
        job: Job,
        limit: int = 25,
    ) -> list[CandidateMatchResult]:
        """
        Get top seeker matches for an employer's job using the same scoring model.

        Includes profiles marked public or "matching orgs only"; hidden profiles
        stay out of employer discovery.
        """
        candidates = cls._retrieve_seekers(job, limit=cls.CANDIDATE_LIMIT)

        results = []
        for seeker, semantic_score in candidates:
            job_result = cls._score_candidate(seeker, job, semantic_score)
            results.append(CandidateMatchResult(
                seeker=seeker,
                job=job,
                score=job_result.score,
                semantic_score=job_result.semantic_score,
                lexical_score=job_result.lexical_score,
                profile_score=job_result.profile_score,
                impact_score=job_result.impact_score,
                impact_tier=job_result.impact_tier,
                reasons=job_result.reasons,
                gaps=job_result.gaps,
                impact_reasons=job_result.impact_reasons,
            ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    @classmethod
    def score_job_for_seeker(
        cls,
        seeker: SeekerProfile,
        job: Job,
    ) -> MatchResult:
        """Score a single job for a seeker, used by the job detail page."""
        semantic_score = cls._semantic_score_for_pair(seeker, job)
        return cls._score_candidate(
            seeker,
            job,
            semantic_score,
            match_context=cls._build_match_context(seeker),
        )

    @classmethod
    def _retrieve_candidates(
        cls,
        seeker: SeekerProfile,
        limit: int = 150,
    ) -> list[tuple[Job, float]]:
        """
        Fast retrieval using HNSW vector index.
        Returns list of (job, semantic_score) tuples.
        """
        query_embedding = seeker.embedding if seeker.embedding is not None else embed_seeker(seeker)

        if query_embedding is None:
            # Fallback: return recent jobs without semantic scoring
            jobs = JobService.get_available_jobs().select_related("organization", "category").order_by('-posted_at', '-id')[:limit]
            return [(job, 50.0) for job in jobs]

        if seeker.embedding is None:
            SeekerProfile.objects.filter(pk=seeker.pk).update(embedding=query_embedding)
            seeker.embedding = query_embedding

        # Vector search with cosine distance
        qs = JobService.get_available_jobs().filter(
            embedding__isnull=False,
        ).select_related(
            "organization", "category",
        ).annotate(
            distance=CosineDistance('embedding', query_embedding)
        ).order_by('distance')[:limit]

        results = []
        for job in qs:
            semantic_score = cls._semantic_score_from_distance(job.distance)
            results.append((job, semantic_score))

        return results

    @classmethod
    def _retrieve_seekers(
        cls,
        job: Job,
        limit: int = 150,
    ) -> list[tuple[SeekerProfile, float]]:
        """Fast seeker retrieval for employer-side candidate matching."""
        job_embedding = job.embedding if job.embedding is not None else embed_job(job)

        if job_embedding is None:
            seekers = SeekerProfile.objects.filter(
                wizard_completed=True,
                is_actively_looking=True,
            ).filter(
                Q(visibility=SeekerProfile.Visibility.PUBLIC) |
                Q(visibility=SeekerProfile.Visibility.MATCHING)
            ).select_related("user").prefetch_related("impact_areas").order_by("-updated_at")[:limit]
            return [(seeker, 50.0) for seeker in seekers]

        qs = SeekerProfile.objects.filter(
            wizard_completed=True,
            is_actively_looking=True,
            embedding__isnull=False,
        ).filter(
            Q(visibility=SeekerProfile.Visibility.PUBLIC) |
            Q(visibility=SeekerProfile.Visibility.MATCHING)
        ).select_related(
            "user",
        ).prefetch_related(
            "impact_areas",
        ).annotate(
            distance=CosineDistance('embedding', job_embedding)
        ).order_by('distance')[:limit]

        return [
            (seeker, cls._semantic_score_from_distance(seeker.distance))
            for seeker in qs
        ]

    @classmethod
    def _semantic_score_for_pair(
        cls,
        seeker: SeekerProfile,
        job: Job,
    ) -> float:
        """Calculate calibrated semantic score for one seeker/job pair."""
        if seeker.embedding is not None and job.embedding is not None:
            distance = Job.objects.filter(pk=job.pk).annotate(
                dist=CosineDistance('embedding', seeker.embedding)
            ).values_list('dist', flat=True).first()
            return cls._semantic_score_from_distance(distance)

        seeker_embedding = seeker.embedding if seeker.embedding is not None else embed_seeker(seeker)
        job_embedding = job.embedding if job.embedding is not None else embed_job(job)

        if not seeker_embedding or not job_embedding:
            return 50.0

        similarity = cls._cosine_similarity(seeker_embedding, job_embedding)
        return cls._semantic_score_from_similarity(similarity)

    @staticmethod
    def _cosine_similarity(left, right) -> float:
        try:
            left_values = [float(value) for value in left]
            right_values = [float(value) for value in right]
            numerator = sum(a * b for a, b in zip(left_values, right_values))
            left_norm = math.sqrt(sum(a * a for a in left_values))
            right_norm = math.sqrt(sum(b * b for b in right_values))
            if not left_norm or not right_norm:
                return 0.0
            return numerator / (left_norm * right_norm)
        except Exception:
            return 0.0

    @classmethod
    def _semantic_score_from_distance(cls, distance: Optional[float]) -> float:
        if distance is None:
            return 50.0
        similarity = 1 - float(distance)
        return cls._semantic_score_from_similarity(similarity)

    @staticmethod
    def _semantic_score_from_similarity(similarity: float) -> float:
        similarity = max(-1.0, min(1.0, float(similarity)))
        from jobs.services.embedding_service import calibration
        cal = calibration()  # cut-offs depend on the embedding model
        span = cal["ceiling"] - cal["floor"]
        score = (similarity - cal["floor"]) / span * 100
        return max(5.0, min(100.0, score))

    @classmethod
    def _score_candidate(
        cls,
        seeker: SeekerProfile,
        job: Job,
        semantic_score: float,
        lexical_score: Optional[float] = None,
        match_context: Optional[dict] = None,
    ) -> MatchResult:
        """
        Score a single candidate with all 4 components.
        """
        job_data_quality = cls._job_data_quality(job)
        semantic_score = min(
            semantic_score,
            cls._semantic_cap_for_job_quality(job_data_quality),
        )

        # Get lexical score via FTS. List pages pass precomputed bulk ranks.
        if lexical_score is None:
            lexical_score = cls._calculate_lexical_score(seeker, job, match_context)

        # Get profile match score
        profile_score, profile_reasons, gaps = cls._calculate_profile_score(
            seeker, job, match_context
        )

        # Get impact potential score
        impact_data = ImpactPotentialService.calculate_impact_potential(seeker, job)
        impact_potential_score = impact_data['score'] * 100
        impact_alignment_score = cls._score_impact_area(seeker, job, match_context=match_context)
        impact_score = (
            impact_alignment_score * 0.55 +
            impact_potential_score * 0.45
        )

        # Calculate weighted final score
        final_score = (
            semantic_score * WEIGHTS['semantic'] +
            lexical_score * WEIGHTS['lexical'] +
            profile_score * WEIGHTS['profile'] +
            impact_score * WEIGHTS['impact']
        )
        final_score = min(
            final_score,
            cls._final_cap_for_job_quality(job_data_quality),
        )

        # Combine all reasons
        all_reasons = profile_reasons.copy()

        # Add semantic match reason
        if semantic_score >= 82:
            all_reasons.insert(0, "Strong semantic match with your profile")
        elif semantic_score >= 68:
            all_reasons.insert(0, "Good relevance to your background")

        if job_data_quality < 0.55:
            all_reasons.append("Match confidence is limited because this job needs more detail")

        return MatchResult(
            job=job,
            score=round(final_score, 1),
            semantic_score=round(semantic_score, 1),
            lexical_score=round(lexical_score, 1),
            profile_score=round(profile_score, 1),
            impact_score=round(impact_score, 1),
            impact_tier=ImpactPotentialService.get_impact_tier(impact_data['score']),
            reasons=all_reasons[:5],
            gaps=gaps[:5],
            impact_reasons=impact_data['reasons'],
        )

    @classmethod
    def _job_data_quality(cls, job: Job) -> float:
        """Estimate how much detail the job has for trustworthy matching."""
        description_len = len((job.description or "").strip())
        requirements_len = len((job.requirements or "").strip())

        score = 0.0

        if description_len >= 1200:
            score += 0.75
        elif description_len >= 700:
            score += 0.65
        elif description_len >= 400:
            score += 0.50
        elif description_len >= 150:
            score += 0.30
        elif description_len >= 80:
            score += 0.12

        if requirements_len >= 300:
            score += 0.10
        elif requirements_len >= 80:
            score += 0.06

        if job.skills:
            score += 0.10

        if job.category and job.category.slug not in {"impact-careers", "other"}:
            score += 0.04

        if job.experience_level:
            score += 0.01

        return max(0.20, min(1.0, score))

    @staticmethod
    def _semantic_cap_for_job_quality(quality: float) -> float:
        if quality >= 0.75:
            return 100.0
        return 30.0 + quality * 55.0

    @staticmethod
    def _final_cap_for_job_quality(quality: float) -> float:
        if quality >= 0.75:
            return 100.0
        return 38.0 + quality * 50.0

    @classmethod
    def _calculate_lexical_score(
        cls,
        seeker: SeekerProfile,
        job: Job,
        match_context: Optional[dict] = None,
    ) -> float:
        """
        Calculate lexical score using PostgreSQL FTS.
        """
        # Build search query from seeker profile
        search_terms = (
            match_context.get("search_terms")
            if match_context
            else cls._build_search_query(seeker)
        )

        if not search_terms or not job.search_vector:
            return 50.0  # Neutral if no search data

        try:
            search_query = SearchQuery(search_terms, search_type='websearch')

            # Get FTS rank for this specific job
            ranked = Job.objects.filter(pk=job.pk).annotate(
                rank=SearchRank('search_vector', search_query)
            ).first()

            if ranked and ranked.rank:
                return cls._lexical_score_from_rank(ranked.rank)
        except Exception:
            pass

        return 50.0

    @staticmethod
    def _lexical_score_from_rank(rank) -> float:
        # Treat FTS as an exact-term bonus. Raw ranks are often tiny, so use
        # log scaling and keep non-matches neutral instead of punishing otherwise
        # good vector matches.
        rank = float(rank or 0)
        if rank <= 0:
            return 50.0
        return min(100.0, 50.0 + math.log1p(rank * 1000) * 10)

    @classmethod
    def _bulk_lexical_scores(cls, job_ids: list[int], search_terms: str) -> dict[int, float]:
        if not job_ids or not search_terms:
            return {}

        try:
            search_query = SearchQuery(search_terms, search_type='websearch')
            rows = (
                Job.objects.filter(pk__in=job_ids)
                .annotate(rank=SearchRank('search_vector', search_query))
                .values_list("pk", "rank")
            )
            return {job_id: cls._lexical_score_from_rank(rank) for job_id, rank in rows}
        except Exception:
            return {}

    @classmethod
    def _build_search_query(cls, seeker: SeekerProfile, impact_areas=None) -> str:
        """Build FTS search query from seeker profile."""
        terms = []

        # Add skills as search terms
        if seeker.skills:
            for slug in seeker.skills[:10]:  # Limit to top 10
                skill = SKILLS_BY_SLUG.get(slug)
                if skill:
                    terms.append(skill.label)
                else:
                    terms.append(slug.replace("-", " "))

        # Add impact areas
        areas = impact_areas if impact_areas is not None else seeker.impact_areas.all()
        for area in list(areas)[:5]:
            terms.append(area.name)

        # Add work style keywords
        styles = seeker.work_styles or ([seeker.work_style] if seeker.work_style else [])
        for style in styles:
            if style in WORK_STYLE_KEYWORDS:
                terms.extend(WORK_STYLE_KEYWORDS[style][:5])

        return " ".join(terms)

    @classmethod
    def _calculate_profile_score(
        cls,
        seeker: SeekerProfile,
        job: Job,
        match_context: Optional[dict] = None,
    ) -> tuple[float, list, list]:
        """
        Calculate profile match score based on structured data.
        Returns (score 0-100, reasons list, gaps list).
        """
        scores = {}
        reasons = []
        gaps = []

        # 1. Impact Area Alignment
        scores['impact_area'] = cls._score_impact_area(
            seeker, job, reasons, match_context=match_context
        )

        # 2. Skills Match
        scores['skills'], skill_gaps = cls._score_skills(seeker, job, reasons)
        gaps.extend(skill_gaps)

        # 3. Experience Level
        scores['experience'] = cls._score_experience(seeker, job, reasons)

        # 4. Work Style
        scores['work_style'] = cls._score_work_style(seeker, job, reasons)

        # 5. Preferences Match
        scores['preferences'] = cls._score_preferences(seeker, job, reasons)

        # Weighted combination
        profile_score = (
            scores['impact_area'] * 0.22 +
            scores['skills'] * 0.38 +
            scores['experience'] * 0.18 +
            scores['work_style'] * 0.12 +
            scores['preferences'] * 0.10
        )

        return profile_score, reasons, gaps

    @classmethod
    def _score_impact_area(
        cls,
        seeker: SeekerProfile,
        job: Job,
        reasons: Optional[list] = None,
        match_context: Optional[dict] = None,
    ) -> float:
        """Score based on impact area alignment."""
        if not job.category:
            return 60.0  # Neutral if no category

        if match_context and "impact_area_ids" in match_context:
            seeker_areas = match_context["impact_area_ids"]
        else:
            seeker_areas = set(seeker.impact_areas.values_list("id", flat=True))

        if not seeker_areas:
            return 60.0  # Neutral if seeker has no preference

        if job.category_id in seeker_areas:
            if reasons is not None:
                reasons.append(f"Matches your {job.category.name} focus")
            return 100.0

        # Category labels are broad and upstream classification can be noisy, so
        # mismatches lower the score without burying an otherwise strong role.
        return 45.0

    @classmethod
    def _build_match_context(cls, seeker: SeekerProfile) -> dict:
        impact_areas = list(seeker.impact_areas.all())
        return {
            "impact_area_ids": {area.id for area in impact_areas},
            "search_terms": cls._build_search_query(seeker, impact_areas=impact_areas),
        }

    @classmethod
    def _matches_cache_key(cls, seeker: SeekerProfile, limit: int) -> str:
        updated_at = seeker.updated_at.timestamp() if seeker.updated_at else 0
        return f"matches:v5:{seeker.pk}:{int(updated_at)}:{limit}"

    @staticmethod
    def _serialize_match(result: MatchResult) -> dict:
        return {
            "job_id": result.job_id if hasattr(result, "job_id") else result.job.id,
            "score": result.score,
            "semantic_score": result.semantic_score,
            "lexical_score": result.lexical_score,
            "profile_score": result.profile_score,
            "impact_score": result.impact_score,
            "impact_tier": result.impact_tier,
            "reasons": result.reasons,
            "gaps": result.gaps,
            "impact_reasons": result.impact_reasons,
        }

    @classmethod
    def _hydrate_cached_matches(cls, cached: list[dict]) -> list[MatchResult]:
        job_ids = [item["job_id"] for item in cached if item.get("job_id")]
        jobs = JobService.get_available_jobs().filter(
            id__in=job_ids,
        ).select_related("organization", "category")
        jobs_by_id = {job.id: job for job in jobs}

        results = []
        for item in cached:
            job = jobs_by_id.get(item.get("job_id"))
            if not job:
                continue
            results.append(MatchResult(
                job=job,
                score=item["score"],
                semantic_score=item["semantic_score"],
                lexical_score=item["lexical_score"],
                profile_score=item["profile_score"],
                impact_score=item["impact_score"],
                impact_tier=item["impact_tier"],
                reasons=item["reasons"],
                gaps=item["gaps"],
                impact_reasons=item["impact_reasons"],
            ))

        return results

    @classmethod
    def _score_skills(
        cls,
        seeker: SeekerProfile,
        job: Job,
        reasons: list,
    ) -> tuple[float, list]:
        """Score based on skills match. Returns (score, gaps)."""
        seeker_skills = cls._normalize_skills(seeker.skills or [])
        job_skills = cls._normalize_skills(job.skills or [])

        if not seeker_skills:
            return 60.0, []  # Neutral if no skills

        if not job_skills:
            # No job skills - do keyword matching on job text
            return cls._score_skills_via_keywords(seeker_skills, job, reasons)

        # Calculate overlap
        overlap = seeker_skills & job_skills
        missing = job_skills - seeker_skills

        if not overlap:
            score = 35.0
        else:
            # Extracted job skills can be noisy and long. Cap denominators so
            # matching 3-5 core skills is rewarded like a real recruiting screen.
            job_denominator = max(1, min(len(job_skills), 8))
            seeker_denominator = max(1, min(len(seeker_skills), 8))
            job_coverage = min(1.0, len(overlap) / job_denominator)
            seeker_coverage = min(1.0, len(overlap) / seeker_denominator)
            score = (job_coverage * 0.72 + seeker_coverage * 0.28) * 100

            if len(overlap) >= 5:
                score = max(score, 86.0)
            elif len(overlap) >= 3:
                score = max(score, 72.0)
            elif len(overlap) == 2:
                score = max(score, 62.0)
            else:
                score = max(score, 50.0)

        if len(overlap) > 0:
            reasons.append(f"{len(overlap)} relevant skill{'s' if len(overlap) != 1 else ''} match this role")

        # Missing skills in the job's own order, leaving out soft skills ("communication" isn't a gap to close).
        from jobs.services.job_service import JobService
        soft = tuple(JobService.SOFT_SKILL_STEMS)
        gaps = []
        for slug in [s for s in (job.skills or []) if s in missing]:
            if any(stem in str(slug).lower() for stem in soft) or "customer-service" in str(slug).lower():
                continue
            gaps.append(cls._skill_label(slug))
            if len(gaps) == 5:
                break

        return score, gaps

    @staticmethod
    def _normalize_skills(skills: list) -> set:
        return {
            str(skill).strip().lower()
            for skill in skills
            if str(skill).strip()
        }

    @staticmethod
    def _skill_label(slug: str) -> str:
        skill = SKILLS_BY_SLUG.get(str(slug))
        if skill:
            return skill.label
        return str(slug).replace("-", " ").replace("_", " ").title()

    @classmethod
    def _score_skills_via_keywords(
        cls,
        seeker_skills: set,
        job: Job,
        reasons: list,
    ) -> tuple[float, list]:
        """Fallback: score skills via keyword matching in job text."""
        job_text = f"{job.title} {job.description or ''} {job.requirements or ''}".lower()

        matches = 0
        for slug in seeker_skills:
            skill = SKILLS_BY_SLUG.get(slug)
            label = skill.label.lower() if skill else slug.replace("-", " ")
            if label in job_text:
                matches += 1
            elif slug.replace("-", " ") in job_text:
                matches += 1

        if matches >= 5:
            reasons.append("Multiple skills mentioned in job")
            return 85.0, []
        elif matches >= 3:
            reasons.append("Some skills found in job description")
            return 70.0, []
        elif matches >= 1:
            return 55.0, []

        return 42.0, []

    @classmethod
    def _score_experience(
        cls,
        seeker: SeekerProfile,
        job: Job,
        reasons: list,
    ) -> float:
        """Score based on experience level match."""
        if not seeker.experience_level:
            return 60.0  # Neutral if no preference

        # Infer job level from title and description
        job_level = cls._infer_job_level(job)

        compatibility = EXPERIENCE_COMPATIBILITY.get(seeker.experience_level, {})

        if not job_level:
            return 65.0  # Unknown

        score = float(compatibility.get(job_level, 50.0))

        if score >= 85:
            reasons.append("Experience level matches")
        elif score >= 70:
            reasons.append("Experience level looks compatible")

        return score

    @classmethod
    def _infer_job_level(cls, job: Job) -> Optional[str]:
        """Infer experience level from job title/description."""
        if job.experience_level:
            mapped_level = JOB_LEVEL_MAP.get(job.experience_level)
            if mapped_level:
                return mapped_level

        text = f"{job.title} {(job.description or '')[:500]}".lower()

        for level, keywords in LEVEL_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    return level

        return None

    @classmethod
    def _score_work_style(
        cls,
        seeker: SeekerProfile,
        job: Job,
        reasons: list,
    ) -> float:
        """Score based on work style match."""
        styles = seeker.work_styles or ([seeker.work_style] if seeker.work_style else [])
        styles = [style for style in styles if style]

        if not styles:
            return 60.0  # Neutral if no preference

        job_text = f"{job.title} {job.description or ''}".lower()
        job_words = set(re.findall(r'\b[a-z]{3,}\b', job_text))

        best_score = 50.0
        best_style = None
        for style in styles:
            style_keywords = WORK_STYLE_KEYWORDS.get(style, [])
            if not style_keywords:
                continue
            matches = 0
            for keyword in style_keywords:
                if " " in keyword:
                    if keyword in job_text:
                        matches += 1
                elif keyword in job_words:
                    matches += 1

            if matches >= 5:
                score = 100.0
            elif matches >= 3:
                score = 82.0
            elif matches >= 1:
                score = 64.0
            else:
                score = 38.0

            if score > best_score:
                best_score = score
                best_style = style

        if best_score >= 90:
            reasons.append(f"Great fit for your {WORK_STYLE_LABELS.get(best_style, best_style)} work style")
        elif best_score >= 75:
            reasons.append(f"Aligns with your {WORK_STYLE_LABELS.get(best_style, best_style)} work style")

        return best_score

    @classmethod
    def _score_preferences(
        cls,
        seeker: SeekerProfile,
        job: Job,
        reasons: list,
    ) -> float:
        """Score based on job type and salary preferences."""
        checks = 0
        matched = 0

        # Job type match
        if seeker.job_types and job.job_type:
            checks += 1
            if job.job_type in seeker.job_types:
                matched += 1
                reasons.append("Job type matches your preference")

        # Salary range match
        if seeker.salary_min and job.salary_max and job.salary_min:
            checks += 1
            seeker_max = seeker.salary_max or float('inf')
            if job.salary_min <= seeker_max and job.salary_max >= seeker.salary_min:
                matched += 1
                reasons.append("Salary in your range")

        if seeker.remote_preference and job.location:
            checks += 1
            location = job.location.lower()
            if seeker.remote_preference in {"remote", "flexible"} and "remote" in location:
                matched += 1

        if checks == 0:
            return 65.0

        return 45.0 + (matched / checks) * 55.0

    @classmethod
    def match_to_dict(cls, result: MatchResult) -> dict:
        """Convert MatchResult to dict for serialization/caching."""
        return {
            'job': result.job,
            'total': result.score,
            'breakdown': {
                'semantic': result.semantic_score,
                'lexical': result.lexical_score,
                'profile': result.profile_score,
                'impact': result.impact_score,
            },
            'impact_tier': result.impact_tier,
            'reasons': result.reasons,
            'gaps': result.gaps,
            'impact_reasons': result.impact_reasons,
        }
