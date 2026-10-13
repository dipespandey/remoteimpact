"""
AI-powered personalized application guide generator.

Generates unique, SEO-optimized content for each job's application guide page.
"""
import json
import logging
import hashlib
from typing import Optional, Dict, Any

from django.conf import settings
from .json_utils import parse_json_object

logger = logging.getLogger(__name__)


def cached_application_guide(job) -> Optional[Dict[str, Any]]:
    """Read a current generated guide without network requests or database writes."""
    raw_data = job.raw_data if isinstance(job.raw_data, dict) else {}
    cached = raw_data.get("application_guide")
    if not isinstance(cached, dict) or cached.get("hash") != _create_job_hash(job):
        return None
    content = cached.get("content")
    return content if isinstance(content, dict) and content else None


def public_application_guide(job):
    """Public GETs must never wait for model generation or mutate a listing."""
    cached = cached_application_guide(job)
    return (cached, True) if cached else (_get_default_content(job), False)


def generate_application_guide(job) -> Dict[str, Any]:
    """
    Generate a personalized application guide for a job.
    
    Returns cached version if available, otherwise generates new content.
    """
    # Check if we have a cached guide
    raw_data = job.raw_data or {}
    cached_guide = raw_data.get("application_guide")
    
    # Create a hash of key job details to detect changes
    job_hash = _create_job_hash(job)
    
    if cached_guide and cached_guide.get("hash") == job_hash:
        return cached_guide.get("content", {})
    
    # Generate new personalized guide
    guide_content = _generate_guide_content(job)
    
    # Cache it
    raw_data["application_guide"] = {
        "hash": job_hash,
        "content": guide_content,
    }
    job.raw_data = raw_data
    job.save(update_fields=["raw_data"])
    
    return guide_content


def _create_job_hash(job) -> str:
    """Create a hash of job details to detect changes."""
    key_data = f"{job.title}|{job.organization.name if job.organization else ''}|{job.description[:500] if job.description else ''}"
    return hashlib.md5(key_data.encode()).hexdigest()[:12]


def _generate_guide_content(job) -> Dict[str, Any]:
    """Generate personalized guide content using AI."""
    import os
    
    org_name = job.organization.name if job.organization else "this organization"
    org_description = ""
    if job.organization and job.organization.description:
        org_description = job.organization.description[:500]
    elif job.company_description:
        org_description = job.company_description[:500]
    
    job_description = job.description[:1500] if job.description else ""
    requirements = job.requirements[:500] if job.requirements else ""
    
    prompt = f"""Generate a personalized application guide for this job. Be specific and helpful.

JOB DETAILS:
- Title: {job.title}
- Company: {org_name}
- Location: {job.location or "Remote"}
- Job Type: {job.get_job_type_display()}
- Company Description: {org_description}
- Job Description: {job_description}
- Requirements: {requirements}

Generate JSON with these fields (be specific to THIS role and company, not generic):

{{
    "company_intro": "2-3 sentences about what makes this company unique and why someone might want to work there",
    "role_overview": "2-3 sentences about what this specific role involves and why it's impactful",
    "ideal_candidate": "3-4 bullet points describing the ideal candidate (be specific based on requirements)",
    "application_tips": [
        "5 specific tips for applying to THIS role at THIS company (not generic advice)"
    ],
    "cover_letter_focus": "3-4 key points to emphasize in a cover letter for this specific role",
    "interview_topics": [
        "5 likely interview topics or questions specific to this role/company"
    ],
    "research_suggestions": [
        "3-4 things the candidate should research about the company before applying"
    ],
    "red_flags_to_avoid": [
        "3 common mistakes candidates make when applying for this type of role"
    ],
    "day_in_life": "2-3 sentences describing what a typical day might look like in this role"
}}

Be specific, actionable, and tailored to this exact position. Avoid generic advice like "proofread your resume".

STYLE RULES:
- Plain, direct English. Short sentences. No marketing adjectives ("exciting", "dynamic", "passionate", "meaningful", "supportive").
- Only state facts that appear in the details above. If something is unknown (team, culture, daily routine), say what to ask or check instead of guessing.
- Never use: unlock, elevate, empower, journey, dream job, stand out, leverage, thrive, game-changer, mission-aligned, exclamation marks.
- Use the job title in normal capitalisation even if it is written in capitals above."""

    # Try different AI providers
    content = None
    
    # Try DeepSeek first (cheapest)
    if os.getenv("DEEPSEEK_API_KEY"):
        content = _call_deepseek(prompt)
    
    if content:
        return content
    
    # Return default content if AI fails
    return _get_default_content(job)


def _call_deepseek(prompt: str) -> Optional[Dict]:
    """Call DeepSeek API."""
    import os
    import httpx

    def request_content(temperature: float) -> str:
        response = httpx.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('DEEPSEEK_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "response_format": {"type": "json_object"},
                "thinking": {"type": "disabled"},
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    try:
        return parse_json_object(request_content(0.7))
    except ValueError:
        # A second deterministic request handles the occasional malformed
        # payload without silently switching providers or returning partial data.
        try:
            return parse_json_object(request_content(0.0))
        except Exception as retry_error:
            logger.warning(f"DeepSeek JSON retry failed: {retry_error}")
            return None
    except Exception as e:
        logger.warning(f"DeepSeek API error: {e}")
        return None


def _get_default_content(job) -> Dict[str, Any]:
    """Fallback when AI generation fails: honest, general guidance with no invented details."""
    from jobs.templatetags.job_extras import display_title

    org_name = job.organization.name if job.organization else "this organization"
    title = display_title(job.title)
    return {
        "company_intro": f"Read {org_name}'s own website and recent updates before applying: what they work on, who funds them and what they have published in the last year.",
        "role_overview": f"The posting for {title} is the best source on what the job involves. Note the three or four responsibilities it mentions first; those usually matter most.",
        "ideal_candidate": [
            "Meets the requirements listed in the posting, or can show equivalent experience",
            "Can point to specific results from similar work",
            "Is eligible to work from the locations the posting allows",
        ],
        "application_tips": [
            f"Name one thing {org_name} has done recently and why it matters to you",
            "Match your CV wording to the responsibilities in the posting",
            "Give numbers where you can: budgets, people, outcomes",
            "Check the location and time-zone limits before applying",
            "Apply through the original posting so your application reaches the employer",
        ],
        "cover_letter_focus": f"Lead with why {org_name}'s work matters to you, then two or three results that match the posting's main responsibilities.",
        "interview_topics": [
            "Why this organization and this role",
            "A recent project like the work in the posting, and what you did",
            "How you work across time zones and with remote colleagues",
            "What you would focus on in your first three months",
            "Your questions about the team, priorities and how success is measured",
        ],
        "research_suggestions": [
            f"{org_name}'s website, annual report or recent publications",
            "Their main programs or products and who they serve",
            "Who leads the team you would join, if it is public",
        ],
        "red_flags_to_avoid": [
            "A generic letter that could be sent to any organization",
            "Ignoring the location or eligibility requirements",
            "Listing duties instead of results",
        ],
        "day_in_life": "The posting doesn't describe a typical day. Ask in the interview how the team plans its week and which meetings the role joins.",
    }
