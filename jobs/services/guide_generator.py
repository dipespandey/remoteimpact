"""
AI-powered personalized application guide generator.

Generates unique, SEO-optimized content for each job's application guide page.
"""
import json
import logging
import hashlib
from typing import Optional, Dict, Any

from django.conf import settings

logger = logging.getLogger(__name__)


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

Be specific, actionable, and tailored to this exact position. Avoid generic advice like "proofread your resume"."""

    # Try different AI providers
    content = None
    
    # Try DeepSeek first (cheapest)
    if os.getenv("DEEPSEEK_API_KEY"):
        content = _call_deepseek(prompt)
    
    # Fall back to Groq
    if not content and os.getenv("GROQ_API_KEY"):
        content = _call_groq(prompt)
    
    # Fall back to OpenAI
    if not content and os.getenv("OPENAI_API_KEY"):
        content = _call_openai(prompt)
    
    if content:
        return content
    
    # Return default content if AI fails
    return _get_default_content(job)


def _call_deepseek(prompt: str) -> Optional[Dict]:
    """Call DeepSeek API."""
    import os
    import httpx
    
    try:
        response = httpx.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('DEEPSEEK_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        logger.warning(f"DeepSeek API error: {e}")
        return None


def _call_groq(prompt: str) -> Optional[Dict]:
    """Call Groq API."""
    import os
    import httpx
    
    try:
        response = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        logger.warning(f"Groq API error: {e}")
        return None


def _call_openai(prompt: str) -> Optional[Dict]:
    """Call OpenAI API."""
    import os
    import httpx
    
    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        logger.warning(f"OpenAI API error: {e}")
        return None


def _get_default_content(job) -> Dict[str, Any]:
    """Return default content if AI generation fails."""
    org_name = job.organization.name if job.organization else "this organization"
    
    return {
        "company_intro": f"{org_name} is an organization focused on making a positive impact. They're looking for dedicated individuals to join their mission-driven team.",
        "role_overview": f"The {job.title} position offers an opportunity to contribute meaningfully while developing your skills in a supportive environment.",
        "ideal_candidate": [
            "Passionate about the organization's mission",
            "Strong communication and collaboration skills",
            "Self-motivated with attention to detail",
            "Eager to learn and grow",
        ],
        "application_tips": [
            f"Research {org_name}'s recent projects and initiatives before applying",
            "Tailor your cover letter to explain why you're drawn to their specific mission",
            "Highlight relevant experience, even if from different sectors",
            "Show concrete examples of your impact in previous roles",
            "Demonstrate your understanding of the challenges in this space",
        ],
        "cover_letter_focus": f"Focus on your alignment with {org_name}'s mission, relevant transferable skills, and specific contributions you could make to the team.",
        "interview_topics": [
            "Your motivation for working in the impact sector",
            "Specific examples of relevant past work",
            "How you handle challenges and ambiguity",
            "Your understanding of the organization's work",
            "Questions about team culture and growth opportunities",
        ],
        "research_suggestions": [
            f"Review {org_name}'s website, recent news, and social media",
            "Understand their key programs and initiatives",
            "Research their leadership team and organizational values",
            "Look for any recent reports or publications they've released",
        ],
        "red_flags_to_avoid": [
            "Generic applications that don't mention the specific organization",
            "Focusing only on what you'll gain rather than what you'll contribute",
            "Not demonstrating knowledge of the sector or mission area",
        ],
        "day_in_life": f"As {job.title}, you'd likely spend your time contributing to meaningful projects, collaborating with mission-aligned colleagues, and seeing the direct impact of your work.",
    }
