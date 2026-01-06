"""
Matching Service for Impact Match.

Calculates match scores between SeekerProfiles and Jobs using text similarity
and structured data matching.

Scoring weights:
- Text Relevance: 35% (keyword overlap between profile & job)
- Impact Area: 25% (category alignment)
- Skills Match: 20% (extracted skills comparison)
- Experience Level: 10% (level compatibility)
- Work Style: 10% (role type matching)
"""

import re
from typing import Optional
from django.db.models import QuerySet

from jobs.models import Job, SeekerProfile, JobMatch, Category
from jobs.constants.skills import SKILLS_BY_SLUG


# Stopwords to filter out common words
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "must", "shall", "can", "need", "dare", "ought", "used",
    "this", "that", "these", "those", "i", "you", "he", "she", "it", "we", "they",
    "what", "which", "who", "whom", "where", "when", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "just", "also",
    "now", "here", "there", "then", "once", "if", "because", "as", "until",
    "while", "about", "against", "between", "into", "through", "during", "before",
    "after", "above", "below", "up", "down", "out", "off", "over", "under", "again",
    "further", "any", "our", "your", "their", "its", "my", "his", "her",
    "work", "job", "role", "position", "team", "company", "organization", "experience",
    "looking", "seeking", "join", "opportunity", "responsibilities", "requirements",
    "qualifications", "skills", "ability", "strong", "excellent", "good", "great",
    "well", "year", "years", "including", "within", "across", "ensure", "support",
    "help", "make", "take", "get", "give", "use", "new", "first", "last", "long",
    "little", "own", "other", "old", "right", "big", "high", "different", "small",
    "large", "next", "early", "young", "important", "public", "bad", "same",
}

# Impact-related keywords that boost relevance
IMPACT_KEYWORDS = {
    "climate", "environment", "sustainability", "carbon", "renewable", "energy",
    "ai", "artificial", "intelligence", "machine", "learning", "safety", "alignment",
    "health", "medical", "healthcare", "disease", "public", "global",
    "poverty", "development", "economic", "equality", "equity", "justice",
    "education", "learning", "teaching", "students", "schools",
    "policy", "government", "advocacy", "nonprofit", "ngo", "philanthropy",
    "animal", "welfare", "rights", "protection",
    "research", "science", "data", "analysis", "evidence",
    "social", "community", "humanitarian", "impact", "mission", "purpose",
    "effective", "altruism", "giving", "charity", "donation",
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


class MatchingService:
    """Service for calculating match scores between seekers and jobs."""

    @staticmethod
    def _extract_keywords(text: str) -> set:
        """Extract meaningful keywords from text."""
        if not text:
            return set()

        # Lowercase and extract words
        text = text.lower()
        words = re.findall(r'\b[a-z]{3,}\b', text)

        # Filter stopwords and short words
        keywords = {w for w in words if w not in STOPWORDS and len(w) >= 3}
        return keywords

    @staticmethod
    def _calculate_text_similarity(seeker_keywords: set, job_keywords: set) -> float:
        """Calculate Jaccard-like similarity with boost for impact keywords."""
        if not seeker_keywords or not job_keywords:
            return 20.0  # Base score even with no data

        # Find overlap
        overlap = seeker_keywords & job_keywords

        if not overlap:
            return 20.0  # Minimum base score

        # Count impact keyword matches (weighted higher)
        impact_overlap = overlap & IMPACT_KEYWORDS
        regular_overlap = overlap - IMPACT_KEYWORDS

        # Weighted overlap: impact keywords count 2x
        weighted_overlap = len(regular_overlap) + (len(impact_overlap) * 2)

        # Use seeker keywords as denominator (what % of seeker interests are in job)
        similarity = weighted_overlap / (len(seeker_keywords) + 1)

        # Also consider what % of job keywords match (but lower weight)
        job_coverage = len(overlap) / (len(job_keywords) + 1)

        # Combine: 60% seeker coverage, 40% job coverage
        combined = (similarity * 0.6) + (job_coverage * 0.4)

        # Scale to 0-100 - boost the multiplier for better spread
        # Add base score of 20 and scale the rest
        score = 20 + min(80, combined * 400)

        return score

    @staticmethod
    def calculate_match(seeker: SeekerProfile, job: Job) -> dict:
        """
        Calculate match score between a seeker and a job.

        Returns dict with:
        - total: overall score 0-100
        - breakdown: {relevance, impact, skills, experience, work_style}
        - reasons: list of match reasons
        - gaps: list of skill gaps
        """
        scores = {}
        reasons = []
        gaps = []

        # Build seeker text profile
        seeker_text_parts = []
        if seeker.impact_statement:
            seeker_text_parts.append(seeker.impact_statement)
        if seeker.skills:
            # Convert skill slugs to labels
            for slug in seeker.skills:
                skill = SKILLS_BY_SLUG.get(slug)
                if skill:
                    seeker_text_parts.append(skill.label)
                else:
                    seeker_text_parts.append(slug.replace("-", " "))
        # Add impact areas
        for area in seeker.impact_areas.all():
            seeker_text_parts.append(area.name)

        seeker_text = " ".join(seeker_text_parts)
        seeker_keywords = MatchingService._extract_keywords(seeker_text)

        # Build job text profile
        job_text = f"{job.title} {job.description or ''} {job.requirements or ''}"
        job_keywords = MatchingService._extract_keywords(job_text)

        # 1. Text Relevance (35%)
        scores["relevance"] = MatchingService._score_text_relevance(
            seeker_keywords, job_keywords, reasons
        )

        # 2. Impact Area Alignment (25%)
        scores["impact"] = MatchingService._score_impact_area(seeker, job, reasons)

        # 3. Skills Match (20%)
        scores["skills"], skill_gaps = MatchingService._score_skills(
            seeker, job, job_keywords, reasons
        )
        gaps.extend(skill_gaps)

        # 4. Experience Level (10%)
        scores["experience"] = MatchingService._score_experience(seeker, job, reasons)

        # 5. Work Style (10%)
        scores["work_style"] = MatchingService._score_work_style(
            seeker, job_keywords, reasons
        )

        # Calculate weighted total
        total = (
            scores["relevance"] * 0.35
            + scores["impact"] * 0.25
            + scores["skills"] * 0.20
            + scores["experience"] * 0.10
            + scores["work_style"] * 0.10
        )

        # Apply boost for high relevance matches
        if scores["relevance"] >= 70 and scores["impact"] >= 80:
            total = min(100, total * 1.1)  # 10% boost for strong matches

        return {
            "total": round(total),
            "breakdown": scores,
            "reasons": reasons[:5],  # Top 5 reasons
            "gaps": gaps[:5],
        }

    @staticmethod
    def _score_text_relevance(seeker_keywords: set, job_keywords: set, reasons: list) -> int:
        """Score based on text similarity between seeker profile and job."""
        score = MatchingService._calculate_text_similarity(seeker_keywords, job_keywords)

        if score >= 70:
            reasons.append("Strong keyword match with your profile")
        elif score >= 50:
            reasons.append("Good alignment with your interests")
        elif score >= 30:
            reasons.append("Some overlap with your background")

        return int(score)

    @staticmethod
    def _score_impact_area(seeker: SeekerProfile, job: Job, reasons: list) -> int:
        """Score based on impact area alignment."""
        if not job.category:
            return 40  # Neutral if no category

        seeker_areas = set(seeker.impact_areas.values_list("slug", flat=True))

        if not seeker_areas:
            return 50  # Neutral if seeker has no preference

        job_category_slug = job.category.slug

        if job_category_slug in seeker_areas:
            reasons.append(f"{job.category.name} matches your focus")
            return 100
        else:
            # No match - but not a hard penalty, some jobs may still be relevant
            return 35

    @staticmethod
    def _score_skills(
        seeker: SeekerProfile, job: Job, job_keywords: set, reasons: list
    ) -> tuple[int, list]:
        """Score based on skills match. Returns (score, gaps)."""
        seeker_skills = set(seeker.skills or [])

        if not seeker_skills:
            return 40, []  # No skills = neutral score

        # Build seeker skill labels for keyword matching
        seeker_skill_labels = set()
        seeker_skill_words = set()
        for slug in seeker_skills:
            skill = SKILLS_BY_SLUG.get(slug)
            if skill:
                seeker_skill_labels.add(skill.label.lower())
                # Also add individual words from multi-word skills
                for word in skill.label.lower().split():
                    if len(word) >= 3:
                        seeker_skill_words.add(word)

        # Get job skills from stored data or extract from text
        job_skills = set()
        if hasattr(job, "skills") and job.skills:
            job_skills = set(job.skills)
        elif job.raw_data and isinstance(job.raw_data, dict):
            job_skills = set(job.raw_data.get("skills", []))

        # Also extract skills from job text
        job_text = f"{job.title} {job.description or ''} {job.requirements or ''}"
        extracted_skills = MatchingService._extract_skills_from_text(job_text)
        job_skills.update(extracted_skills)

        # Always do keyword matching as primary/fallback method
        # Check how many seeker skill words appear in job keywords
        keyword_matches = len(seeker_skill_words & job_keywords)

        if not job_skills:
            # No structured job skills found - use keyword matching only
            if keyword_matches >= 5:
                reasons.append(f"Skills keywords found")
                return 85, []
            elif keyword_matches >= 3:
                reasons.append(f"Some skills mentioned")
                return 70, []
            elif keyword_matches >= 1:
                return 55, []
            return 40, []

        # Calculate structured skill overlap
        overlap = seeker_skills & job_skills
        missing = job_skills - seeker_skills

        if len(job_skills) > 0:
            match_ratio = len(overlap) / len(job_skills)
        else:
            match_ratio = 0.5

        # Base score from structured match
        score = int(match_ratio * 100)

        # Boost from keyword matches if structured match is low
        if score < 50 and keyword_matches >= 2:
            score = max(score, 50 + keyword_matches * 5)

        # Minimum score of 35 (some transferable skills likely)
        score = max(35, score)

        # Boost if we have multiple matches
        if len(overlap) >= 5:
            score = min(100, score + 10)

        if len(overlap) > 0:
            reasons.append(f"{len(overlap)} of {len(job_skills)} skills match")
        elif keyword_matches >= 2:
            reasons.append(f"Related skills found")

        # Convert missing skill slugs to labels for gaps
        gaps = []
        for slug in list(missing)[:5]:
            skill = SKILLS_BY_SLUG.get(slug)
            if skill:
                gaps.append(skill.label)
            else:
                gaps.append(slug.replace("-", " ").title())

        return score, gaps

    @staticmethod
    def _extract_skills_from_text(text: str) -> set:
        """Extract skill slugs from text by keyword matching."""
        text_lower = text.lower()
        found_skills = set()

        for slug, skill in SKILLS_BY_SLUG.items():
            # Check for skill label in text
            if skill.label.lower() in text_lower:
                found_skills.add(slug)
            # Also check for slug (with hyphens replaced)
            elif slug.replace("-", " ") in text_lower:
                found_skills.add(slug)

        return found_skills

    @staticmethod
    def _score_experience(seeker: SeekerProfile, job: Job, reasons: list) -> int:
        """Score based on experience level match."""
        if not seeker.experience_level:
            return 50  # Neutral if no preference

        # Infer job level from title and description
        job_level = MatchingService._infer_job_level(job)

        compatible_levels = EXPERIENCE_COMPATIBILITY.get(seeker.experience_level, [])

        if job_level in compatible_levels:
            reasons.append(f"Experience level matches")
            return 100
        elif job_level:
            # Mismatch
            return 30
        else:
            # Can't determine job level - neutral
            return 60

    @staticmethod
    def _infer_job_level(job: Job) -> Optional[str]:
        """Infer experience level required from job title/description."""
        text = f"{job.title} {(job.description or '')[:500]}".lower()

        for level, keywords in LEVEL_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    return level

        return None

    @staticmethod
    def _score_work_style(seeker: SeekerProfile, job_keywords: set, reasons: list) -> int:
        """Score based on work style match."""
        if not seeker.work_style:
            return 50  # Neutral if no preference

        style_keywords = WORK_STYLE_KEYWORDS.get(seeker.work_style, [])

        if not style_keywords:
            return 50

        # Count how many work style keywords appear in job
        matches = sum(1 for kw in style_keywords if kw in job_keywords)

        if matches >= 5:
            reasons.append(f"Great fit for your work style")
            return 100
        elif matches >= 3:
            reasons.append(f"Aligns with your work style")
            return 80
        elif matches >= 1:
            return 60
        else:
            return 30

    @classmethod
    def get_matches_for_seeker(
        cls,
        seeker: SeekerProfile,
        jobs: Optional[QuerySet] = None,
        min_score: int = 0,
        limit: int = 50,
        scan_limit: int = 1000,
    ) -> list[dict]:
        """
        Get top job matches for a seeker.

        Args:
            seeker: The seeker profile to match against
            jobs: Optional queryset of jobs to scan (defaults to all active)
            min_score: Minimum score to include in results
            limit: Maximum number of results to return
            scan_limit: Maximum number of jobs to scan (for performance)

        Returns list of dicts with job and match data, sorted by score.
        """
        if jobs is None:
            jobs = Job.objects.filter(is_active=True).select_related(
                "organization", "category"
            )

        matches = []
        for job in jobs[:scan_limit]:
            match_data = cls.calculate_match(seeker, job)
            if match_data["total"] >= min_score:
                matches.append({"job": job, **match_data})

        # Sort by score descending
        matches.sort(key=lambda x: x["total"], reverse=True)

        # Return top N matches
        return matches[:limit]

    @classmethod
    def cache_match(cls, seeker: SeekerProfile, job: Job) -> JobMatch:
        """Calculate and cache a match score."""
        match_data = cls.calculate_match(seeker, job)

        job_match, created = JobMatch.objects.update_or_create(
            seeker=seeker,
            job=job,
            defaults={
                "score": match_data["total"],
                "breakdown": match_data["breakdown"],
                "reasons": match_data["reasons"],
                "gaps": match_data["gaps"],
            },
        )
        return job_match

    @classmethod
    def get_cached_match(
        cls, seeker: SeekerProfile, job: Job
    ) -> Optional[JobMatch]:
        """Get cached match if it exists and is recent."""
        try:
            return JobMatch.objects.get(seeker=seeker, job=job)
        except JobMatch.DoesNotExist:
            return None

    @classmethod
    def get_or_calculate_match(cls, seeker: SeekerProfile, job: Job) -> dict:
        """Get cached match or calculate fresh."""
        # Always calculate fresh for now to reflect algorithm changes
        # Can re-enable caching once algorithm is stable
        return cls.calculate_match(seeker, job)
