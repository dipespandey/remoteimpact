"""
Unified Matching Service for Remote Impact.

Single source of truth for all job-seeker matching, combining:
- Semantic similarity (35%) - embedding cosine distance
- Lexical matching (15%) - PostgreSQL full-text search
- Profile matching (30%) - skills, experience, preferences
- Impact potential (20%) - org credibility + role leverage

Pipeline:
1. RETRIEVE: HNSW vector search for top 150 candidates (fast, high recall)
2. SCORE: Re-rank with all 4 scoring components
3. RETURN: Top 25 with detailed breakdown
"""

import re
from dataclasses import dataclass
from typing import Optional

from django.contrib.postgres.search import SearchQuery, SearchRank
from pgvector.django import CosineDistance

from jobs.models import Job, SeekerProfile, Category
from jobs.services.embedding_service import embed_seeker
from jobs.services.impact_potential_service import ImpactPotentialService
from jobs.constants.skills import SKILLS_BY_SLUG


# Scoring weights (must sum to 1.0)
WEIGHTS = {
    'semantic': 0.35,       # Embedding similarity - backbone of matching
    'lexical': 0.15,        # Full-text search - exact term matching
    'profile': 0.30,        # Skills, experience, preferences
    'impact': 0.20,         # Org credibility + role leverage
}

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
    "early": ["entry", "junior", "associate", "early", "graduate", "intern"],
    "mid": ["mid", "intermediate", "associate", "senior"],
    "senior": ["senior", "lead", "staff", "principal", "mid"],
    "leadership": ["director", "head", "vp", "chief", "manager", "lead", "senior", "executive"],
    "career_changer": ["entry", "junior", "associate", "mid", "early"],
}

LEVEL_KEYWORDS = {
    "entry": ["entry", "junior", "graduate", "intern", "trainee", "early career"],
    "mid": ["mid-level", "mid level", "intermediate", "associate", "2-5 years", "3-5 years"],
    "senior": ["senior", "lead", "staff", "principal", "sr.", "sr ", "5+ years", "7+ years"],
    "leadership": ["director", "head of", "vp", "chief", "manager", "cto", "ceo", "coo", "executive"],
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


class UnifiedMatchingService:
    """Single source of truth for job-seeker matching."""

    # Retrieval parameters
    CANDIDATE_LIMIT = 150      # How many to retrieve from HNSW
    FINAL_LIMIT = 25           # How many to return after re-ranking

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
        # Stage 1: Retrieve candidates via vector search
        candidates = cls._retrieve_candidates(seeker, limit=cls.CANDIDATE_LIMIT)

        if not candidates:
            return []

        # Stage 2: Score each candidate with all components
        results = []
        for job, semantic_score in candidates:
            result = cls._score_candidate(seeker, job, semantic_score)
            results.append(result)

        # Stage 3: Sort by final score and return top N
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

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
        query_embedding = embed_seeker(seeker)

        if query_embedding is None:
            # Fallback: return recent jobs without semantic scoring
            jobs = Job.objects.filter(
                is_active=True
            ).order_by('-posted_at')[:limit]
            return [(job, 50.0) for job in jobs]

        # Vector search with cosine distance
        qs = Job.objects.filter(
            is_active=True,
            embedding__isnull=False,
        ).annotate(
            distance=CosineDistance('embedding', query_embedding)
        ).order_by('distance')[:limit]

        results = []
        for job in qs:
            # Convert distance to similarity score (0-100)
            semantic_score = (1 - job.distance) * 100
            results.append((job, semantic_score))

        return results

    @classmethod
    def _score_candidate(
        cls,
        seeker: SeekerProfile,
        job: Job,
        semantic_score: float,
    ) -> MatchResult:
        """
        Score a single candidate with all 4 components.
        """
        # Get lexical score via FTS
        lexical_score = cls._calculate_lexical_score(seeker, job)

        # Get profile match score
        profile_score, profile_reasons, gaps = cls._calculate_profile_score(seeker, job)

        # Get impact potential score
        impact_data = ImpactPotentialService.calculate_impact_potential(seeker, job)
        impact_score = impact_data['score'] * 100  # Convert to 0-100

        # Calculate weighted final score
        final_score = (
            semantic_score * WEIGHTS['semantic'] +
            lexical_score * WEIGHTS['lexical'] +
            profile_score * WEIGHTS['profile'] +
            impact_score * WEIGHTS['impact']
        )

        # Combine all reasons
        all_reasons = profile_reasons.copy()

        # Add semantic match reason
        if semantic_score >= 80:
            all_reasons.insert(0, "Strong semantic match with your profile")
        elif semantic_score >= 65:
            all_reasons.insert(0, "Good relevance to your background")

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
    def _calculate_lexical_score(
        cls,
        seeker: SeekerProfile,
        job: Job,
    ) -> float:
        """
        Calculate lexical score using PostgreSQL FTS.
        """
        # Build search query from seeker profile
        search_terms = cls._build_search_query(seeker)

        if not search_terms or not job.search_vector:
            return 50.0  # Neutral if no search data

        try:
            search_query = SearchQuery(search_terms, search_type='websearch')

            # Get FTS rank for this specific job
            ranked = Job.objects.filter(pk=job.pk).annotate(
                rank=SearchRank('search_vector', search_query)
            ).first()

            if ranked and ranked.rank:
                # Normalize rank to 0-100 (FTS ranks are typically 0-1)
                # Use log scaling since ranks can have long tail
                score = min(100, ranked.rank * 100)
                return score
        except Exception:
            pass

        return 50.0

    @classmethod
    def _build_search_query(cls, seeker: SeekerProfile) -> str:
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
        for area in seeker.impact_areas.all()[:5]:
            terms.append(area.name)

        # Add work style keywords
        if seeker.work_style and seeker.work_style in WORK_STYLE_KEYWORDS:
            terms.extend(WORK_STYLE_KEYWORDS[seeker.work_style][:5])

        return " ".join(terms)

    @classmethod
    def _calculate_profile_score(
        cls,
        seeker: SeekerProfile,
        job: Job,
    ) -> tuple[float, list, list]:
        """
        Calculate profile match score based on structured data.
        Returns (score 0-100, reasons list, gaps list).
        """
        scores = {}
        reasons = []
        gaps = []

        # 1. Impact Area Alignment (27% of profile score = 8% of total)
        scores['impact_area'] = cls._score_impact_area(seeker, job, reasons)

        # 2. Skills Match (33% of profile score = 10% of total)
        scores['skills'], skill_gaps = cls._score_skills(seeker, job, reasons)
        gaps.extend(skill_gaps)

        # 3. Experience Level (17% of profile score = 5% of total)
        scores['experience'] = cls._score_experience(seeker, job, reasons)

        # 4. Work Style (13% of profile score = 4% of total)
        scores['work_style'] = cls._score_work_style(seeker, job, reasons)

        # 5. Preferences Match (10% of profile score = 3% of total)
        scores['preferences'] = cls._score_preferences(seeker, job, reasons)

        # Weighted combination
        profile_score = (
            scores['impact_area'] * 0.27 +
            scores['skills'] * 0.33 +
            scores['experience'] * 0.17 +
            scores['work_style'] * 0.13 +
            scores['preferences'] * 0.10
        )

        return profile_score, reasons, gaps

    @classmethod
    def _score_impact_area(
        cls,
        seeker: SeekerProfile,
        job: Job,
        reasons: list,
    ) -> float:
        """Score based on impact area alignment."""
        if not job.category:
            return 50.0  # Neutral if no category

        seeker_areas = set(seeker.impact_areas.values_list("id", flat=True))

        if not seeker_areas:
            return 50.0  # Neutral if seeker has no preference

        if job.category_id in seeker_areas:
            reasons.append(f"Matches your {job.category.name} focus")
            return 100.0

        # Partial credit for related categories could be added here
        return 35.0

    @classmethod
    def _score_skills(
        cls,
        seeker: SeekerProfile,
        job: Job,
        reasons: list,
    ) -> tuple[float, list]:
        """Score based on skills match. Returns (score, gaps)."""
        seeker_skills = set(seeker.skills or [])
        job_skills = set(job.skills or [])

        if not seeker_skills:
            return 50.0, []  # Neutral if no skills

        if not job_skills:
            # No job skills - do keyword matching on job text
            return cls._score_skills_via_keywords(seeker_skills, job, reasons)

        # Calculate overlap
        overlap = seeker_skills & job_skills
        missing = job_skills - seeker_skills

        if not job_skills:
            return 50.0, []

        match_ratio = len(overlap) / len(job_skills)
        score = match_ratio * 100

        # Minimum score of 35 (some transferable skills likely)
        score = max(35.0, score)

        if len(overlap) >= 3:
            score = min(100, score + 10)  # Bonus for multiple matches

        if len(overlap) > 0:
            reasons.append(f"{len(overlap)} of {len(job_skills)} required skills match")

        # Convert missing slugs to labels
        gaps = []
        for slug in list(missing)[:5]:
            skill = SKILLS_BY_SLUG.get(slug)
            if skill:
                gaps.append(skill.label)
            else:
                gaps.append(slug.replace("-", " ").title())

        return score, gaps

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
            if skill and skill.label.lower() in job_text:
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

        return 40.0, []

    @classmethod
    def _score_experience(
        cls,
        seeker: SeekerProfile,
        job: Job,
        reasons: list,
    ) -> float:
        """Score based on experience level match."""
        if not seeker.experience_level:
            return 50.0  # Neutral if no preference

        # Infer job level from title and description
        job_level = cls._infer_job_level(job)

        compatible_levels = EXPERIENCE_COMPATIBILITY.get(seeker.experience_level, [])

        if job_level and job_level in compatible_levels:
            reasons.append("Experience level matches")
            return 100.0
        elif job_level:
            return 30.0  # Mismatch
        else:
            return 60.0  # Unknown

    @classmethod
    def _infer_job_level(cls, job: Job) -> Optional[str]:
        """Infer experience level from job title/description."""
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
        if not seeker.work_style:
            return 50.0  # Neutral if no preference

        style_keywords = WORK_STYLE_KEYWORDS.get(seeker.work_style, [])
        if not style_keywords:
            return 50.0

        job_text = f"{job.title} {job.description or ''}".lower()
        job_words = set(re.findall(r'\b[a-z]{3,}\b', job_text))

        matches = sum(1 for kw in style_keywords if kw in job_words)

        if matches >= 5:
            reasons.append("Great fit for your work style")
            return 100.0
        elif matches >= 3:
            reasons.append("Aligns with your work style")
            return 80.0
        elif matches >= 1:
            return 60.0

        return 30.0

    @classmethod
    def _score_preferences(
        cls,
        seeker: SeekerProfile,
        job: Job,
        reasons: list,
    ) -> float:
        """Score based on job type and salary preferences."""
        score = 50.0  # Start neutral
        matched = 0

        # Job type match
        if seeker.job_types and job.job_type:
            if job.job_type in seeker.job_types:
                matched += 1
                score += 25

        # Salary range match
        if seeker.salary_min and job.salary_max and job.salary_min:
            seeker_max = seeker.salary_max or float('inf')
            if job.salary_min <= seeker_max and job.salary_max >= seeker.salary_min:
                matched += 1
                score += 25
                reasons.append("Salary in your range")

        return min(100.0, score)

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
