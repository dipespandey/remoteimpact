"""
LLM-powered candidate scoring engine for mission-fit evaluation.
Scores applications on mission alignment, skills match, and culture fit.
"""

import json
import logging
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from jobs.models import Application, ApplicationScore, JobApplicationResponse, MissionScreeningQuestion
from .ai import AIClient

logger = logging.getLogger(__name__)


class ScoringError(Exception):
    """Raised when candidate scoring fails."""
    pass


class CandidateScoringEngine:
    """
    AI-powered candidate scoring that evaluates:
    1. Mission alignment - candidate's commitment to org mission
    2. Skills match - technical and soft skills
    3. Culture fit - team dynamics, work style, values
    4. Overall recommendation - should we interview?
    """

    def __init__(self):
        self.ai_client = AIClient()
        self.min_score = Decimal("0")
        self.max_score = Decimal("100")

    def score_application(
        self,
        application: Application,
        force_rescore: bool = False,
    ) -> ApplicationScore:
        """
        Generate AI score for an application.
        
        Args:
            application: Application instance to score
            force_rescore: Bypass cache and rescore
            
        Returns:
            ApplicationScore instance (created or updated)
            
        Raises:
            ScoringError: If scoring fails
        """
        # Check if already scored
        if not force_rescore:
            try:
                score = application.hire_for_mission_score
                logger.info(f"Application {application.id} already scored")
                return score
            except ApplicationScore.DoesNotExist:
                pass

        try:
            job = application.job
            applicant = application.applicant
            org = job.organization
            
            # Build scoring context
            context = self._build_scoring_context(application, job, org)
            
            # Call LLM for scoring
            scores = self._call_llm_for_scoring(context)
            
            # Calculate overall score and recommendation
            overall_score, recommendation = self._calculate_overall_score(scores)
            
            # Create or update score record
            score_obj, created = ApplicationScore.objects.update_or_create(
                application=application,
                defaults={
                    "overall_score": overall_score,
                    "mission_alignment_score": scores["mission_alignment"],
                    "skills_match_score": scores["skills_match"],
                    "culture_fit_score": scores["culture_fit"],
                    "scoring_breakdown": scores.get("breakdown", {}),
                    "recommendation": recommendation,
                    "recommendation_reason": scores.get("recommendation_reason", ""),
                    "llm_model": self.ai_client.model,
                    "llm_cost_usd": Decimal("0.0002"),  # Rough estimate
                    "updated_at": timezone.now(),
                }
            )
            
            logger.info(
                f"Scored application {application.id} "
                f"(score={overall_score}, recommendation={recommendation})"
            )
            return score_obj
            
        except Exception as e:
            logger.error(f"Scoring failed for application {application.id}: {e}")
            raise ScoringError(f"Failed to score application: {str(e)}")

    def _build_scoring_context(
        self,
        application: Application,
        job,
        org,
    ) -> dict:
        """Build comprehensive context for scoring."""
        # Get applicant profile info
        applicant = application.applicant
        profile = getattr(applicant, 'seeker_profile', None) or getattr(applicant, 'profile', None)
        
        # Get screening responses
        responses = JobApplicationResponse.objects.filter(
            application=application
        ).select_related('question')
        response_text = "\n".join([
            f"Q: {r.question.question_text}\nA: {r.response_text}"
            for r in responses
        ])
        
        # Get skills from job and profile
        job_skills = job.skills or []
        profile_skills = getattr(profile, 'skills', []) if profile else []
        
        return {
            "applicant_name": applicant.get_full_name() or applicant.email,
            "applicant_bio": getattr(profile, 'bio', '') if profile else '',
            "applicant_years_experience": getattr(profile, 'years_experience', 0) if profile else 0,
            "applicant_headline": getattr(profile, 'headline', '') if profile else '',
            "applicant_skills": profile_skills[:10] if profile_skills else [],
            
            "org_name": org.name,
            "org_mission": org.impact_statement,
            "org_type": org.get_organization_type_display() if org.organization_type else "Unknown",
            
            "job_title": job.title,
            "job_description": job.description[:1500],
            "job_impact": job.impact,
            "job_requirements": job.requirements[:1500],
            "job_skills": job_skills,
            
            "cover_letter": application.cover_letter,
            "screening_responses": response_text,
        }

    def _call_llm_for_scoring(self, context: dict) -> dict:
        """
        Call LLM to score the candidate.
        
        Returns:
            Dict with mission_alignment, skills_match, culture_fit, and reasoning
        """
        prompt = self._build_scoring_prompt(context)
        
        try:
            response_text = self.ai_client.generate(prompt, max_tokens=2000)
            scores = self._parse_scoring_response(response_text)
            return scores
        except Exception as e:
            logger.error(f"LLM scoring call failed: {e}")
            raise

    def _build_scoring_prompt(self, context: dict) -> str:
        """Build the prompt for LLM scoring."""
        return f"""
You are an expert recruiter for mission-driven organizations.
Score this candidate on three dimensions, each 0-100.

ORGANIZATION:
- Name: {context['org_name']}
- Type: {context['org_type']}
- Mission: {context['org_mission']}

JOB:
- Title: {context['job_title']}
- Impact: {context['job_impact']}
- Requirements: {context['job_requirements'][:800]}
- Key Skills: {', '.join(context['job_skills'][:5])}

CANDIDATE:
- Name: {context['applicant_name']}
- Experience: {context['applicant_years_experience']} years
- Skills: {', '.join(context['applicant_skills'][:5]) if context['applicant_skills'] else 'Not specified'}
- Bio: {context['applicant_headline']}
- Cover Letter/Why: {context['cover_letter'][:500] if context['cover_letter'] else 'Not provided'}
- Responses to Screening Questions:
{context['screening_responses'][:1000] if context['screening_responses'] else 'No responses yet'}

SCORING CRITERIA:
1. Mission Alignment (0-100): How committed is this candidate to {context['org_name']}'s mission? Do their values align?
2. Skills Match (0-100): How well do their skills match the job requirements?
3. Culture Fit (0-100): Will they thrive in this team/work environment?

Return ONLY valid JSON with no markdown code blocks:
{{
  "mission_alignment": <0-100>,
  "skills_match": <0-100>,
  "culture_fit": <0-100>,
  "recommendation_reason": "Brief summary of key strengths and any concerns",
  "breakdown": {{
    "mission_strengths": ["strength 1", "strength 2"],
    "mission_concerns": ["concern 1"] if any,
    "skills_strengths": ["strength 1"],
    "skills_gaps": ["gap 1"] if any,
    "culture_fit_observations": "2-3 sentence observation"
  }}
}}

Be fair and constructive. Scores should reflect realistic assessment.
"""

    def _parse_scoring_response(self, response_text: str) -> dict:
        """Parse LLM response into structured scores."""
        try:
            response_text = response_text.strip()
            
            # Extract JSON if wrapped in markdown
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            
            response_text = response_text.strip()
            scores_raw = json.loads(response_text)
            
            # Validate and normalize scores
            scores = {
                "mission_alignment": self._validate_score(scores_raw.get("mission_alignment", 50)),
                "skills_match": self._validate_score(scores_raw.get("skills_match", 50)),
                "culture_fit": self._validate_score(scores_raw.get("culture_fit", 50)),
                "recommendation_reason": scores_raw.get("recommendation_reason", ""),
                "breakdown": scores_raw.get("breakdown", {}),
            }
            
            return scores
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse scoring response: {e}")
            logger.error(f"Raw response: {response_text[:500]}")
            raise ScoringError(f"Invalid JSON from LLM: {str(e)}")

    def _validate_score(self, score) -> Decimal:
        """Validate and normalize a score to 0-100."""
        try:
            score_val = Decimal(str(score))
            if score_val < 0:
                score_val = Decimal("0")
            elif score_val > 100:
                score_val = Decimal("100")
            return score_val.quantize(Decimal("0.01"))
        except (ValueError, TypeError):
            return Decimal("50")  # Default middle score

    def _calculate_overall_score(
        self,
        scores: dict,
    ) -> tuple[Decimal, str]:
        """
        Calculate overall score and generate recommendation.
        
        Returns:
            Tuple of (overall_score, recommendation)
        """
        # Weighted average: 35% mission, 40% skills, 25% culture
        mission = Decimal(str(scores.get("mission_alignment", 50)))
        skills = Decimal(str(scores.get("skills_match", 50)))
        culture = Decimal(str(scores.get("culture_fit", 50)))
        
        overall = (mission * Decimal("0.35") + 
                  skills * Decimal("0.40") + 
                  culture * Decimal("0.25"))
        overall = overall.quantize(Decimal("0.01"))
        
        # Generate recommendation based on score and alignment
        if overall >= 85:
            recommendation = "strong_yes"
        elif overall >= 70:
            recommendation = "yes"
        elif overall >= 50:
            recommendation = "maybe"
        else:
            recommendation = "no"
        
        # Downgrade if mission alignment is too low
        if mission < 40:
            recommendation = "no"
        elif recommendation == "strong_yes" and mission < 60:
            recommendation = "yes"
        
        return overall, recommendation

    def bulk_score_applications(
        self,
        applications: list[Application],
        batch_size: int = 10,
    ) -> list[ApplicationScore]:
        """
        Score multiple applications with rate limiting.
        
        Args:
            applications: List of Application instances
            batch_size: Number to score before pause
            
        Returns:
            List of ApplicationScore objects
        """
        scores = []
        for idx, app in enumerate(applications):
            try:
                score = self.score_application(app)
                scores.append(score)
                
                if (idx + 1) % batch_size == 0:
                    logger.info(f"Scored {idx + 1}/{len(applications)} applications")
                    
            except ScoringError as e:
                logger.warning(f"Failed to score application {app.id}: {e}")
                continue
        
        return scores

    def get_top_candidates(
        self,
        job,
        limit: int = 10,
    ) -> list[ApplicationScore]:
        """
        Get top-scored candidates for a job.
        
        Args:
            job: Job instance
            limit: Number of top candidates to return
            
        Returns:
            List of ApplicationScore objects sorted by score
        """
        return ApplicationScore.objects.filter(
            application__job=job
        ).order_by("-overall_score")[:limit]

    def get_shortlist(
        self,
        job,
        min_score: Decimal = Decimal("70"),
        max_results: int = 20,
    ) -> list[ApplicationScore]:
        """
        Get qualified shortlist (score >= threshold).
        
        Args:
            job: Job instance
            min_score: Minimum score to include (default 70)
            max_results: Maximum results
            
        Returns:
            List of qualified ApplicationScore objects
        """
        return ApplicationScore.objects.filter(
            application__job=job,
            overall_score__gte=min_score,
        ).select_related(
            'application__applicant',
        ).order_by('-overall_score')[:max_results]
