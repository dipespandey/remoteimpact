"""
LLM-powered question generator for mission-fit screening.
Generates contextual screening questions based on job and organization details.
"""

import json
import logging
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.core.cache import cache

from jobs.models import Job, Organization, MissionScreeningQuestion
from .ai import AIClient

logger = logging.getLogger(__name__)


class QuestionGenerationError(Exception):
    """Raised when question generation fails."""
    pass


class MissionQuestionGenerator:
    """
    Generates AI-powered screening questions focused on:
    1. Mission alignment - candidate's commitment to the org's cause
    2. Culture fit - team dynamics, work style
    3. Skills validation - core technical/soft skills
    4. Experience - relevant background
    """

    def __init__(self):
        self.ai_client = AIClient()
        self.cache_ttl = 86400  # 24 hours

    def generate_questions_for_job(
        self,
        job: Job,
        num_questions: int = 4,
        force_regenerate: bool = False,
    ) -> list[MissionScreeningQuestion]:
        """
        Generate screening questions for a job.
        
        Args:
            job: Job instance to generate questions for
            num_questions: Number of questions to generate
            force_regenerate: Bypass cache and regenerate
            
        Returns:
            List of created MissionScreeningQuestion objects
            
        Raises:
            QuestionGenerationError: If generation fails
        """
        cache_key = f"screening_questions_{job.id}"
        
        # Check cache first
        if not force_regenerate:
            cached_questions = cache.get(cache_key)
            if cached_questions:
                logger.info(f"Using cached questions for job {job.id}")
                return cached_questions

        try:
            # Get org context
            org = job.organization
            context = self._build_org_context(org, job)
            
            # Generate questions using LLM
            questions_data = self._call_llm_for_questions(
                job=job,
                context=context,
                num_questions=num_questions,
            )
            
            # Store questions in database
            created_questions = self._save_questions(job, questions_data)
            
            # Cache the questions
            cache.set(cache_key, created_questions, self.cache_ttl)
            
            logger.info(f"Generated {len(created_questions)} questions for job {job.id}")
            return created_questions
            
        except Exception as e:
            logger.error(f"Question generation failed for job {job.id}: {e}")
            raise QuestionGenerationError(f"Failed to generate questions: {str(e)}")

    def _build_org_context(self, org: Organization, job: Job) -> dict:
        """Build context about the organization for prompt."""
        return {
            "org_name": org.name,
            "org_description": org.description,
            "org_mission": org.impact_statement,
            "org_type": org.get_organization_type_display() if org.organization_type else "Unknown",
            "org_impact_metric": f"{org.impact_metric_name}: {org.impact_metric_value}",
            "job_title": job.title,
            "job_impact": job.impact,
            "job_description": job.description[:1000],  # First 1000 chars
            "required_skills": job.skills[:5] if job.skills else [],
        }

    def _call_llm_for_questions(
        self,
        job: Job,
        context: dict,
        num_questions: int = 4,
    ) -> list[dict]:
        """
        Call LLM to generate screening questions.
        
        Returns:
            List of dicts with question_type, question_text, and context
        """
        # Split questions by type for better distribution
        types_count = {
            "mission_alignment": max(1, num_questions // 3),
            "culture_fit": max(1, num_questions // 3),
            "skills_validation": max(1, num_questions - (num_questions // 3) * 2),
        }

        prompt = self._build_prompt(context, types_count)

        try:
            response_text = self.ai_client.generate(prompt, max_tokens=2000)
            questions_data = self._parse_llm_response(response_text, job, context)
            
            if not questions_data:
                raise QuestionGenerationError("LLM returned empty questions")
                
            return questions_data
            
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

    def _build_prompt(self, context: dict, types_count: dict) -> str:
        """Build the prompt for LLM question generation."""
        return f"""
You are an expert recruiter specializing in mission-driven organizations.
Generate screening questions that help evaluate mission alignment, culture fit, and skills for this job.

ORGANIZATION CONTEXT:
- Name: {context['org_name']}
- Type: {context['org_type']}
- Mission: {context['org_mission']}
- Impact: {context['org_impact_metric']}

JOB CONTEXT:
- Title: {context['job_title']}
- Impact Statement: {context['job_impact']}
- Required Skills: {', '.join(context['required_skills']) if context['required_skills'] else 'General'}

TASK:
Generate exactly {types_count['mission_alignment']} mission alignment questions,
{types_count['culture_fit']} culture fit questions,
and {types_count['skills_validation']} skills validation questions.

Requirements:
1. Questions should be open-ended and thoughtful
2. Avoid yes/no questions
3. Each question should reveal valuable insights
4. Tailor to the organization's mission
5. Keep questions concise (1-2 sentences max)

Return ONLY valid JSON array with no markdown code blocks:
[
  {{"type": "mission_alignment", "text": "Question about commitment to {context['org_name']}'s mission..."}},
  {{"type": "culture_fit", "text": "Question about collaboration and team fit..."}},
  {{"type": "skills_validation", "text": "Question about relevant skills..."}}
]

Remember: Return ONLY the JSON array, no other text.
"""

    def _parse_llm_response(self, response_text: str, job: Job, context: dict) -> list[dict]:
        """
        Parse LLM response into structured questions.
        
        Args:
            response_text: Raw response from LLM
            job: Job instance
            context: Context dict
            
        Returns:
            List of question dicts
        """
        try:
            # Try to extract JSON from response
            response_text = response_text.strip()
            
            # If wrapped in markdown code block, extract it
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            
            response_text = response_text.strip()
            
            questions_raw = json.loads(response_text)
            
            if not isinstance(questions_raw, list):
                raise ValueError("Expected JSON array")
            
            # Transform to our format
            questions = []
            for idx, q in enumerate(questions_raw):
                question_type = q.get("type", "").replace("-", "_")
                
                # Validate question type
                valid_types = [
                    "mission_alignment",
                    "culture_fit", 
                    "skills_validation",
                ]
                if question_type not in valid_types:
                    question_type = valid_types[idx % len(valid_types)]
                
                questions.append({
                    "question_type": question_type,
                    "question_text": q.get("text", ""),
                    "context": context,
                })
            
            return questions
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}")
            logger.error(f"Raw response: {response_text[:500]}")
            raise QuestionGenerationError(f"Invalid JSON from LLM: {str(e)}")

    def _save_questions(
        self,
        job: Job,
        questions_data: list[dict],
    ) -> list[MissionScreeningQuestion]:
        """Save generated questions to database."""
        created_questions = []
        
        # Delete old questions
        job.screening_questions.all().delete()
        
        for q_data in questions_data:
            question = MissionScreeningQuestion.objects.create(
                job=job,
                question_type=q_data["question_type"],
                question_text=q_data["question_text"],
                context=q_data.get("context", {}),
                llm_model=self.ai_client.model,
                llm_cost_usd=Decimal("0.0001"),  # Rough estimate
                is_active=True,
            )
            created_questions.append(question)
        
        return created_questions

    def regenerate_for_job(self, job: Job) -> list[MissionScreeningQuestion]:
        """Regenerate questions for a job, bypassing cache."""
        return self.generate_questions_for_job(
            job=job,
            force_regenerate=True,
        )

    def get_or_generate(self, job: Job) -> list[MissionScreeningQuestion]:
        """
        Get existing questions or generate new ones.
        
        Returns:
            List of screening questions (from DB or newly generated)
        """
        # Check if we already have questions
        existing = job.screening_questions.filter(is_active=True)
        if existing.exists():
            return list(existing)
        
        # Generate new ones
        return self.generate_questions_for_job(job)
