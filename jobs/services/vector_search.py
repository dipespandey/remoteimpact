def search_jobs_for_seeker(seeker, limit=50):
    """Backward-compatible wrapper around the unified matching service.

    Returns the historical tuple shape:
    (job, final_0_to_1, semantic_0_to_1, lexical_0_to_1, profile_0_to_1)
    """
    from jobs.services.unified_matching_service import UnifiedMatchingService

    results = UnifiedMatchingService.get_matches(seeker, limit=limit)
    return [
        (
            result.job,
            result.score / 100,
            result.semantic_score / 100,
            result.lexical_score / 100,
            result.profile_score / 100,
        )
        for result in results
    ]


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
    """Backward-compatible wrapper around employer-side unified matching."""
    from jobs.services.unified_matching_service import UnifiedMatchingService

    results = UnifiedMatchingService.get_candidate_matches(job, limit=limit)
    return [
        (
            result.seeker,
            result.score / 100,
            result.semantic_score / 100,
            result.lexical_score / 100,
            result.profile_score / 100,
        )
        for result in results
    ]
