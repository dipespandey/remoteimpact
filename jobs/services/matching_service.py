"""
Matching Service for Impact Match.

Calculates match scores between SeekerProfiles and Jobs.
Scoring weights:
- Impact area alignment: 25%
- Skills match: 30%
- Experience level: 20%
- Location/Remote: 15%
- Salary fit: 10%
"""

from typing import Optional
from django.db.models import QuerySet

from jobs.models import Job, SeekerProfile, JobMatch, Category
from jobs.constants.skills import SKILLS_BY_SLUG


# Experience level compatibility matrix
# Key: seeker level, Value: list of compatible job levels (inferred from title/description)
EXPERIENCE_COMPATIBILITY = {
    "early": ["entry", "junior", "associate", "early"],
    "mid": ["mid", "intermediate", "associate", "senior"],
    "senior": ["senior", "lead", "staff", "principal", "mid"],
    "leadership": ["director", "head", "vp", "chief", "manager", "lead", "senior"],
    "career_changer": ["entry", "junior", "associate", "mid", "early"],  # Flexible
}

# Keywords that indicate experience level in job titles/descriptions
LEVEL_KEYWORDS = {
    "entry": ["entry", "junior", "graduate", "intern", "trainee"],
    "mid": ["mid-level", "intermediate", "associate"],
    "senior": ["senior", "lead", "staff", "principal", "sr.", "sr "],
    "leadership": ["director", "head of", "vp", "chief", "manager", "cto", "ceo", "coo"],
}


class MatchingService:
    """Service for calculating match scores between seekers and jobs."""

    @staticmethod
    def calculate_match(seeker: SeekerProfile, job: Job) -> dict:
        """
        Calculate match score between a seeker and a job.

        Returns dict with:
        - total: overall score 0-100
        - breakdown: {impact, skills, experience, location, salary}
        - reasons: list of match reasons
        - gaps: list of skill gaps
        """
        scores = {}
        reasons = []
        gaps = []

        # 1. Impact Area Alignment (25%)
        scores["impact"] = MatchingService._score_impact_area(seeker, job, reasons)

        # 2. Skills Match (30%)
        scores["skills"], skill_gaps = MatchingService._score_skills(seeker, job, reasons)
        gaps.extend(skill_gaps)

        # 3. Experience Level (20%)
        scores["experience"] = MatchingService._score_experience(seeker, job, reasons)

        # 4. Location/Remote (15%)
        scores["location"] = MatchingService._score_location(seeker, job, reasons)

        # 5. Salary Fit (10%)
        scores["salary"] = MatchingService._score_salary(seeker, job, reasons)

        # Calculate weighted total
        total = (
            scores["impact"] * 0.25
            + scores["skills"] * 0.30
            + scores["experience"] * 0.20
            + scores["location"] * 0.15
            + scores["salary"] * 0.10
        )

        return {
            "total": round(total),
            "breakdown": scores,
            "reasons": reasons,
            "gaps": gaps[:5],  # Top 5 gaps only
        }

    @staticmethod
    def _score_impact_area(seeker: SeekerProfile, job: Job, reasons: list) -> int:
        """Score based on impact area alignment."""
        if not job.category:
            return 50  # Neutral if no category

        seeker_areas = set(seeker.impact_areas.values_list("slug", flat=True))
        job_category_slug = job.category.slug

        if job_category_slug in seeker_areas:
            reasons.append(f"{job.category.name} matches your interests")
            return 100
        elif seeker_areas:
            # Partial match - check for related areas
            return 40
        else:
            return 50  # No preference set

    @staticmethod
    def _score_skills(seeker: SeekerProfile, job: Job, reasons: list) -> tuple[int, list]:
        """Score based on skills match. Returns (score, gaps)."""
        seeker_skills = set(seeker.skills or [])

        # Get job skills from raw_data or extracted skills
        job_skills = set()
        if hasattr(job, "skills") and job.skills:
            job_skills = set(job.skills)
        elif job.raw_data and isinstance(job.raw_data, dict):
            job_skills = set(job.raw_data.get("skills", []))

        # Also try to extract skills from requirements text
        if not job_skills and job.requirements:
            job_skills = MatchingService._extract_skills_from_text(job.requirements)

        if not job_skills:
            # No job skills to match against
            if seeker_skills:
                reasons.append("Your skills look relevant")
                return 70, []
            return 50, []

        # Calculate overlap
        overlap = seeker_skills & job_skills
        missing = job_skills - seeker_skills

        if len(job_skills) > 0:
            match_ratio = len(overlap) / len(job_skills)
        else:
            match_ratio = 0.5

        score = int(match_ratio * 100)

        if len(overlap) > 0:
            reasons.append(f"{len(overlap)} of {len(job_skills)} required skills")

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
            return 50  # No preference

        # Infer job level from title and description
        job_level = MatchingService._infer_job_level(job)

        compatible_levels = EXPERIENCE_COMPATIBILITY.get(seeker.experience_level, [])

        if job_level in compatible_levels:
            reasons.append(f"{seeker.get_experience_level_display()} level matches")
            return 100
        elif job_level:
            # Some match but not ideal
            return 60
        else:
            # Can't determine job level
            return 70

    @staticmethod
    def _infer_job_level(job: Job) -> Optional[str]:
        """Infer experience level required from job title/description."""
        text = f"{job.title} {job.description[:500]}".lower()

        for level, keywords in LEVEL_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    return level

        return None

    @staticmethod
    def _score_location(seeker: SeekerProfile, job: Job, reasons: list) -> int:
        """Score based on location/remote preference match."""
        # All jobs on this board are remote, so check remote preference
        job_location = (job.location or "").lower()

        if not seeker.remote_preference:
            return 80  # No preference, assume OK

        if seeker.remote_preference == "remote":
            if "remote" in job_location:
                reasons.append("Fully remote")
                return 100
            return 70

        if seeker.remote_preference == "hybrid":
            if "hybrid" in job_location or "flexible" in job_location:
                reasons.append("Hybrid work available")
                return 100
            if "remote" in job_location:
                return 80
            return 60

        if seeker.remote_preference == "onsite":
            if "onsite" in job_location or "office" in job_location:
                reasons.append("On-site position")
                return 100
            return 50

        # "flexible" preference
        reasons.append("Remote work available")
        return 90

    @staticmethod
    def _score_salary(seeker: SeekerProfile, job: Job, reasons: list) -> int:
        """Score based on salary fit."""
        if not seeker.salary_min and not seeker.salary_max:
            return 70  # No preference

        if not job.salary_min and not job.salary_max:
            return 60  # Can't determine

        job_min = float(job.salary_min or 0)
        job_max = float(job.salary_max or job_min)
        seeker_min = float(seeker.salary_min or 0)
        seeker_max = float(seeker.salary_max or seeker_min * 1.5)

        # Check for overlap
        if job_max < seeker_min:
            # Job pays less than seeker wants
            diff_pct = ((seeker_min - job_max) / seeker_min) * 100
            if diff_pct > 20:
                return 30
            reasons.append(f"Salary range is {int(diff_pct)}% below target")
            return 50

        if job_min > seeker_max:
            # Job pays more (this is usually fine)
            reasons.append("Salary exceeds your range")
            return 90

        # Overlap exists
        reasons.append("Salary range matches")
        return 100

    @classmethod
    def get_matches_for_seeker(
        cls,
        seeker: SeekerProfile,
        jobs: Optional[QuerySet] = None,
        min_score: int = 0,
        limit: int = 100,
    ) -> list[dict]:
        """
        Get all job matches for a seeker.

        Returns list of dicts with job and match data, sorted by score.
        """
        if jobs is None:
            jobs = Job.objects.filter(is_active=True).select_related(
                "organization", "category"
            )

        matches = []
        for job in jobs[:limit]:
            match_data = cls.calculate_match(seeker, job)
            if match_data["total"] >= min_score:
                matches.append({"job": job, **match_data})

        # Sort by score descending
        matches.sort(key=lambda x: x["total"], reverse=True)
        return matches

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
        cached = cls.get_cached_match(seeker, job)
        if cached:
            return {
                "total": cached.score,
                "breakdown": cached.breakdown,
                "reasons": cached.reasons,
                "gaps": cached.gaps,
            }
        return cls.calculate_match(seeker, job)
