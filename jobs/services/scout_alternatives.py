"""Older source-backed listings for an empty preview, never match/email results."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from jobs.services.job_service import JobService
from jobs.services.scout_constraints import evaluate_job
from jobs.services.scout_recovery import duty_evidence


def older_openings(preferences, *, limit=3):
    """Bounded, no API calls, no relaxed constraints, no recent rejected jobs."""
    titles = [t for t in preferences.get('titles', []) if isinstance(t, str) and 3 <= len(t) <= 100][:5]
    if not titles or limit <= 0 or preferences.get('unresolved_constraints'):
        return []
    now = timezone.now()
    title_query = Q()
    for title in titles:
        title_query |= Q(title__icontains=title)
    qs = JobService.get_available_jobs().filter(
        title_query, posted_at__gte=now - timedelta(days=90), posted_at__lt=now - timedelta(days=30)
    ).select_related('organization', 'category').defer('embedding', 'search_vector').order_by('-posted_at', '-pk')
    from jobs.services.job_scout_service import JobScoutService
    categories = [slug for slug in preferences.get('required_category_slugs', preferences.get('category_slugs', [])) if slug in JobScoutService.HARD_CATEGORY_SLUGS]
    if categories:
        qs = qs.filter(category__slug__in=((preferences.get('required_category_options') or preferences['category_slugs'])
                       if preferences.get('category_match_mode') == 'any' else categories))
    picked, seen = [], set()
    for job in qs[:60]:
        if not evaluate_job(job, preferences, relaxed=False).allowed:
            continue
        quote = duty_evidence(job, preferences)
        if not quote:
            continue
        identity = JobScoutService._repost_key(job)
        if identity in seen:
            continue
        job.alternative_evidence = quote
        seen.add(identity)
        picked.append(job)
        if len(picked) >= min(limit, 3):
            break
    # Reuse the existing remote-policy gate, with network review disabled.
    from jobs.services.scout_remote_policy import check_matches
    return check_matches(picked, preferences, {}, allow_api=False)
