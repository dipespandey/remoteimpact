from django.contrib.postgres.search import SearchQuery, SearchRank
from pgvector.django import CosineDistance

from jobs.models import Job
from jobs.services.embedding_service import embed_seeker, build_search_query


def search_jobs_for_seeker(seeker, limit=50):
    query_embedding = embed_seeker(seeker)
    search_terms = build_search_query(seeker)

    if not query_embedding:
        return [(job, 0, 0, 0, 0) for job in Job.objects.filter(is_active=True).order_by('-posted_at')[:limit]]

    search_query = SearchQuery(search_terms, search_type='websearch') if search_terms else None

    qs = Job.objects.filter(is_active=True, embedding__isnull=False)
    qs = qs.annotate(distance=CosineDistance('embedding', query_embedding))

    if search_query:
        qs = qs.annotate(fts_rank=SearchRank('search_vector', search_query))

    jobs = list(qs.order_by('distance')[:limit * 3])

    results = []
    max_fts = max((getattr(j, 'fts_rank', 0) for j in jobs), default=1) or 1

    for job in jobs:
        semantic = 1 - job.distance
        fts = getattr(job, 'fts_rank', 0) / max_fts
        text_score = (semantic * 0.6) + (fts * 0.4)
        structured = compute_structured_score(seeker, job)
        final = (text_score * 0.6) + (structured * 0.4)
        results.append((job, final, semantic, fts, structured))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:limit]


def compute_structured_score(seeker, job):
    score = 0.0

    if job.category_id and seeker.impact_areas.filter(id=job.category_id).exists():
        score += 0.35

    if seeker.skills and job.skills:
        overlap = len(set(seeker.skills) & set(job.skills)) / len(job.skills)
        score += 0.25 * min(overlap * 2, 1.0)

    if seeker.salary_min and job.salary_max and job.salary_min:
        if job.salary_min <= seeker.salary_max and job.salary_max >= seeker.salary_min:
            score += 0.20

    if seeker.job_types and job.job_type in seeker.job_types:
        score += 0.20

    return score


def search_candidates_for_job(job, limit=50):
    from jobs.models import SeekerProfile
    from jobs.services.embedding_service import embed_job, build_job_search_query

    job_embedding = job.embedding or embed_job(job)
    search_terms = build_job_search_query(job)

    search_query = SearchQuery(search_terms, search_type='websearch') if search_terms else None

    qs = SeekerProfile.objects.filter(visibility='public', is_actively_looking=True, embedding__isnull=False)
    qs = qs.annotate(distance=CosineDistance('embedding', job_embedding))

    if search_query:
        qs = qs.annotate(fts_rank=SearchRank('search_vector', search_query))

    seekers = list(qs.order_by('distance')[:limit * 3])

    results = []
    max_fts = max((getattr(s, 'fts_rank', 0) for s in seekers), default=1) or 1

    for seeker in seekers:
        semantic = 1 - seeker.distance
        fts = getattr(seeker, 'fts_rank', 0) / max_fts
        text_score = (semantic * 0.6) + (fts * 0.4)
        structured = compute_structured_score(seeker, job)
        final = (text_score * 0.6) + (structured * 0.4)
        results.append((seeker, final, semantic, fts, structured))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:limit]
