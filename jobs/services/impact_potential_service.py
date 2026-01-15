"""
Impact Potential Service

Calculates the "Impact Potential" score for a job-seeker match based on:
1. Organization credibility signals (verified third-party endorsements)
2. Role leverage (organizational influence inferred from job title)
3. Skill scarcity (how rare/valuable the seeker's skills are for impact sector)
"""

import re
from typing import Optional

from jobs.models import Job, Organization, SeekerProfile


# Role leverage keywords with approximate organizational influence scores
# Higher = more leverage/influence within organization
ROLE_LEVERAGE_KEYWORDS = {
    # Executive/Leadership (25-30 points)
    'ceo': 30, 'chief': 28, 'founder': 28, 'president': 27,
    'executive director': 30, 'managing director': 28,
    'vp': 25, 'vice president': 25,

    # Director/Head (18-22 points)
    'director': 22, 'head of': 22, 'head,': 20,
    'principal': 20, 'partner': 20,

    # Senior/Lead (12-18 points)
    'senior': 15, 'lead': 16, 'staff': 14,
    'manager': 14, 'team lead': 16,

    # Mid-level (8-12 points)
    'specialist': 10, 'analyst': 10, 'engineer': 10,
    'coordinator': 8, 'associate': 8,

    # Entry-level (4-8 points)
    'junior': 6, 'assistant': 5, 'intern': 4, 'fellow': 6,
}

# Skill scarcity multipliers - skills rare in impact sector get higher scores
# Based on typical supply-demand in nonprofit/impact space
SCARCE_SKILLS = {
    # Very scarce in impact sector (1.5x multiplier)
    'machine learning': 1.5, 'ml': 1.5, 'ai': 1.5, 'artificial intelligence': 1.5,
    'data science': 1.4, 'data engineering': 1.4, 'devops': 1.4,
    'cybersecurity': 1.5, 'security': 1.3,
    'blockchain': 1.4, 'web3': 1.3,
    'product management': 1.3, 'product': 1.2,

    # Moderately scarce (1.2-1.3x multiplier)
    'python': 1.2, 'javascript': 1.2, 'typescript': 1.2,
    'react': 1.2, 'node': 1.2, 'aws': 1.3, 'gcp': 1.3, 'azure': 1.3,
    'sql': 1.1, 'postgresql': 1.2, 'data analysis': 1.2,
    'ux': 1.3, 'ui': 1.2, 'design': 1.2,

    # Common in impact sector (1.0x - baseline)
    'fundraising': 1.0, 'grant writing': 1.0, 'communications': 1.0,
    'advocacy': 1.0, 'policy': 1.0, 'research': 1.0,
    'project management': 1.0, 'operations': 1.0,
}


class ImpactPotentialService:
    """Service for calculating impact potential scores."""

    # Score component weights (should sum to 100)
    ORG_CREDIBILITY_WEIGHT = 40  # 0-40 points
    ROLE_LEVERAGE_WEIGHT = 30    # 0-30 points
    SKILL_SCARCITY_WEIGHT = 30   # 0-30 points

    @classmethod
    def calculate_impact_potential(
        cls,
        seeker: SeekerProfile,
        job: Job
    ) -> dict:
        """
        Calculate the overall impact potential score for a job-seeker match.

        Returns dict with:
            - score: float 0-1 (overall impact potential)
            - org_credibility: float 0-1
            - role_leverage: float 0-1
            - skill_scarcity: float 0-1
            - reasons: list of human-readable explanations
        """
        org_score, org_reasons = cls.calculate_org_credibility(job.organization)
        role_score, role_reasons = cls.calculate_role_leverage(job.title)
        scarcity_score, scarcity_reasons = cls.calculate_skill_scarcity(
            seeker.skills or [], job.skills or []
        )

        # Weighted sum (each component already 0-1)
        final_score = (
            (org_score * cls.ORG_CREDIBILITY_WEIGHT / 100) +
            (role_score * cls.ROLE_LEVERAGE_WEIGHT / 100) +
            (scarcity_score * cls.SKILL_SCARCITY_WEIGHT / 100)
        )

        all_reasons = org_reasons + role_reasons + scarcity_reasons

        return {
            'score': final_score,
            'org_credibility': org_score,
            'role_leverage': role_score,
            'skill_scarcity': scarcity_score,
            'reasons': all_reasons,
        }

    @classmethod
    def calculate_org_credibility(cls, org: Organization) -> tuple[float, list]:
        """
        Score organization based on verified third-party signals.
        Returns (score 0-1, list of reasons).
        """
        points = 0
        reasons = []

        # Third-party endorsements
        if org.is_givewell_top_charity:
            points += 25  # Highest signal - rigorous evaluation
            reasons.append("GiveWell Top Charity (rigorous impact evaluation)")

        if org.is_80k_recommended:
            points += 18  # Strong signal - vetted for impact
            reasons.append("Recommended by 80,000 Hours")

        if org.is_bcorp_certified:
            # Scale by B Corp score if available
            if org.bcorp_score and org.bcorp_score >= 130:
                points += 15
                reasons.append(f"B Corp Certified (Score: {org.bcorp_score})")
            elif org.bcorp_score and org.bcorp_score >= 100:
                points += 12
                reasons.append(f"B Corp Certified (Score: {org.bcorp_score})")
            else:
                points += 10
                reasons.append("B Corp Certified")

        # Transparency signals
        if org.has_public_impact_report:
            points += 8
            reasons.append("Publishes impact reports")

        if org.has_public_financials:
            points += 5
            reasons.append("Transparent financials")

        # Profile completeness bonus (up to 4 points)
        if org.impact_statement:
            points += 2
        if org.impact_metric_name and org.impact_metric_value:
            points += 2

        # Normalize to 0-1 (max possible ~55 points)
        score = min(points / 50, 1.0)

        return score, reasons

    @classmethod
    def calculate_role_leverage(cls, job_title: str) -> tuple[float, list]:
        """
        Infer organizational leverage from job title.
        Higher-level roles have more impact potential.
        Returns (score 0-1, list of reasons).
        """
        if not job_title:
            return 0.3, []  # Default mid-level assumption

        title_lower = job_title.lower()
        max_score = 0
        matched_keyword = None

        for keyword, points in ROLE_LEVERAGE_KEYWORDS.items():
            if keyword in title_lower:
                if points > max_score:
                    max_score = points
                    matched_keyword = keyword

        # Default to mid-level if no keywords matched
        if max_score == 0:
            max_score = 10

        # Normalize to 0-1 (max is 30 points)
        score = max_score / 30

        reasons = []
        if matched_keyword and max_score >= 20:
            reasons.append(f"Senior-level role with high organizational leverage")
        elif matched_keyword and max_score >= 14:
            reasons.append(f"Mid-senior role with moderate organizational leverage")

        return score, reasons

    @classmethod
    def calculate_skill_scarcity(
        cls,
        seeker_skills: list,
        job_skills: list
    ) -> tuple[float, list]:
        """
        Score based on how scarce the seeker's matching skills are.
        Skills rare in impact sector = higher counterfactual value.
        Returns (score 0-1, list of reasons).
        """
        if not seeker_skills or not job_skills:
            return 0.5, []  # Neutral if no skill data

        seeker_set = set(s.lower() for s in seeker_skills)
        job_set = set(s.lower() for s in job_skills)

        # Find overlapping skills
        overlap = seeker_set & job_set

        if not overlap:
            return 0.3, []  # Low score if no skill match

        # Calculate scarcity-weighted score
        total_multiplier = 0
        scarce_skills_found = []

        for skill in overlap:
            multiplier = 1.0
            for scarce_skill, mult in SCARCE_SKILLS.items():
                if scarce_skill in skill or skill in scarce_skill:
                    multiplier = max(multiplier, mult)
                    if mult >= 1.3:
                        scarce_skills_found.append(skill)
                    break
            total_multiplier += multiplier

        # Average multiplier across matching skills
        avg_multiplier = total_multiplier / len(overlap)

        # Convert to 0-1 score (baseline 1.0 = 0.5, max 1.5 = 1.0)
        score = min((avg_multiplier - 0.5) / 1.0, 1.0)

        reasons = []
        if scarce_skills_found:
            skills_str = ', '.join(scarce_skills_found[:3])
            reasons.append(f"Your skills ({skills_str}) are in high demand for impact roles")

        return score, reasons

    @classmethod
    def get_impact_tier(cls, score: float) -> str:
        """Get human-readable tier from impact potential score."""
        if score >= 0.75:
            return "exceptional"
        elif score >= 0.55:
            return "high"
        elif score >= 0.35:
            return "moderate"
        else:
            return "standard"
