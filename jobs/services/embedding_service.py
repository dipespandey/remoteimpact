from sentence_transformers import SentenceTransformer

_model = None


def get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer('all-MiniLM-L6-v2')
    return _model


def embed(text):
    return get_model().encode(text, normalize_embeddings=True).tolist()


def embed_job(job):
    parts = [job.title, job.description or '']
    if job.requirements:
        parts.append(job.requirements)
    if job.impact:
        parts.append(job.impact)
    return embed(' '.join(parts)[:8000])


def embed_seeker(seeker):
    parts = []
    if seeker.impact_statement:
        parts.append(seeker.impact_statement)
    if seeker.impact_areas.exists():
        parts.append('Impact areas: ' + ', '.join(c.name for c in seeker.impact_areas.all()))
    if seeker.skills:
        parts.append('Skills: ' + ', '.join(seeker.skills))
    if seeker.work_style:
        parts.append(f'Work style: {seeker.work_style}')
    return embed(' '.join(parts)) if parts else None


def build_search_query(seeker):
    terms = []
    if seeker.skills:
        terms.extend(seeker.skills[:10])
    if seeker.impact_areas.exists():
        terms.extend(c.name for c in seeker.impact_areas.all()[:5])
    if seeker.work_style:
        terms.append(seeker.work_style)
    return ' OR '.join(terms) if terms else ''


def build_job_search_query(job):
    terms = []
    if job.skills:
        terms.extend(job.skills[:10])
    if job.category:
        terms.append(job.category.name)
    return ' OR '.join(terms) if terms else ''
