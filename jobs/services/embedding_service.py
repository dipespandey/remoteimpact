from jobs.constants.skills import SKILLS_BY_SLUG


_model = None


WORK_STYLE_LABELS = {
    "builder": "Building things: engineering, product, design",
    "strategist": "Moving ideas: strategy, communications, policy",
    "operator": "Running operations: operations, finance, HR, administration",
    "direct": "Direct service: program delivery, field work, community work",
    "researcher": "Research and analysis",
}

EXPERIENCE_LABELS = {
    "early": "Early career, 0-2 years",
    "mid": "Mid-level, 3-6 years",
    "senior": "Senior, 7-12 years",
    "leadership": "Leadership, 12+ years",
    "career_changer": "Career changer",
}

REMOTE_LABELS = {
    "remote": "Fully remote",
    "hybrid": "Hybrid",
    "onsite": "On-site",
    "flexible": "Flexible",
}


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer('all-MiniLM-L6-v2')
    return _model


def embed(text):
    return get_model().encode(text, normalize_embeddings=True).tolist()


def get_embedding(text):
    """
    Get embedding for a text string (e.g., search query).
    Returns None if embedding fails.
    """
    if not text or not text.strip():
        return None
    try:
        return embed(text.strip()[:1000])  # Limit query length
    except Exception:
        return None


def _clean_text(value, max_chars=None):
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if max_chars:
        return text[:max_chars]
    return text


def _skill_label(slug):
    skill = SKILLS_BY_SLUG.get(str(slug))
    if skill:
        return skill.label
    return str(slug).replace("-", " ").replace("_", " ").title()


def _skill_labels(slugs, limit=20):
    return [_skill_label(slug) for slug in (slugs or [])[:limit]]


def _choice_display(obj, field_name, fallback=None):
    getter = getattr(obj, f"get_{field_name}_display", None)
    if callable(getter):
        value = getter()
        if value:
            return value
    return fallback


def embed_job(job):
    parts = [
        f"Role title: {_clean_text(job.title)}",
    ]

    if job.organization_id and job.organization:
        parts.append(f"Organization: {_clean_text(job.organization.name)}")

    if job.category_id and job.category:
        parts.append(f"Impact area: {_clean_text(job.category.name)}")

    job_type = _choice_display(job, "job_type", job.job_type)
    if job_type:
        parts.append(f"Employment type: {_clean_text(job_type)}")

    experience = _choice_display(job, "experience_level", job.experience_level)
    if experience:
        parts.append(f"Experience level: {_clean_text(experience)}")

    if job.country:
        parts.append(f"Location eligibility: {_clean_text(job.country)}")

    if job.skills:
        parts.append("Required skills: " + ", ".join(_skill_labels(job.skills, limit=25)))

    if job.impact:
        parts.append("Role impact: " + _clean_text(job.impact, 1200))

    if job.requirements:
        parts.append("Requirements: " + _clean_text(job.requirements, 1800))

    if job.description:
        parts.append("Job description: " + _clean_text(job.description, 5000))

    if job.benefits:
        parts.append("Benefits and culture: " + _clean_text(job.benefits, 800))

    return embed(' '.join(parts)[:8000])


def embed_seeker(seeker):
    parts = []
    if seeker.headline:
        parts.append("Professional headline: " + _clean_text(seeker.headline))
    if seeker.bio:
        parts.append("Professional bio: " + _clean_text(seeker.bio, 1800))
    if seeker.impact_statement:
        parts.append("Impact motivation: " + _clean_text(seeker.impact_statement, 800))
    if seeker.impact_areas.exists():
        parts.append('Impact areas: ' + ', '.join(c.name for c in seeker.impact_areas.all()))
    if seeker.skills:
        parts.append('Skills: ' + ', '.join(_skill_labels(seeker.skills, limit=30)))

    styles = seeker.work_styles or ([seeker.work_style] if seeker.work_style else [])
    if styles:
        parts.append('Work styles: ' + ', '.join(WORK_STYLE_LABELS.get(style, style) for style in styles))

    if seeker.experience_level:
        parts.append(f"Experience stage: {EXPERIENCE_LABELS.get(seeker.experience_level, seeker.experience_level)}")

    if seeker.job_types:
        parts.append("Preferred job types: " + ", ".join(seeker.job_types))

    if seeker.remote_preference:
        parts.append(f"Remote preference: {REMOTE_LABELS.get(seeker.remote_preference, seeker.remote_preference)}")

    if seeker.country_eligibility:
        parts.append(f"Work eligibility: {_clean_text(seeker.country_eligibility)}")

    if seeker.assessment_answers:
        answers = []
        for key, value in seeker.assessment_answers.items():
            if value:
                answers.append(f"{key}: {value}")
        if answers:
            parts.append("Additional preferences: " + "; ".join(answers[:8]))

    return embed(' '.join(parts)) if parts else None


def build_search_query(seeker):
    terms = []
    if seeker.skills:
        terms.extend(_skill_labels(seeker.skills, limit=10))
    if seeker.impact_areas.exists():
        terms.extend(c.name for c in seeker.impact_areas.all()[:5])
    styles = seeker.work_styles or ([seeker.work_style] if seeker.work_style else [])
    for style in styles:
        terms.append(WORK_STYLE_LABELS.get(style, style))
    return ' OR '.join(terms) if terms else ''


def build_job_search_query(job):
    terms = []
    if job.skills:
        terms.extend(_skill_labels(job.skills, limit=10))
    if job.category:
        terms.append(job.category.name)
    if job.title:
        terms.append(job.title)
    return ' OR '.join(terms) if terms else ''
