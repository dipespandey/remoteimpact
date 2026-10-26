"""Source-backed exploration options, kept separate from match/email results."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from jobs.services.job_service import JobService
from jobs.services.scout_constraints import evaluate_job
from jobs.services.scout_recovery import duty_evidence


def explore_openings(preferences, *, limit=3, exclude_ids=()):
    """No dead-end screen: rank current real openings when matching is empty.

    These are browsing options, not verified matches. Never weaken the matcher,
    write deliveries, spend a free Scout credit, or call a provider here.
    """
    from django.db.models import Case, When, Value, IntegerField
    from jobs.services.job_scout_service import JobScoutService
    from jobs.services.scout_constraints import CAVEAT_TEXT, location_compatibility, salary_compatible, salary_caveat
    from jobs.services.scout_evidence import compensation_display, location_evidence_warning
    from jobs.services.scout_source_profile import request_terms, function_priority
    from jobs.services.scout_source_evidence import load_original_sources
    if limit <= 0:
        return []
    terms = sorted(request_terms(preferences))[:8]
    title_query, body_query = Q(pk__in=[]), Q(pk__in=[])
    for term in terms:
        title_query |= Q(title__icontains=term)
        body_query |= Q(description__icontains=term)
    qs = JobService.get_available_jobs().exclude(pk__in=list(exclude_ids)).select_related('organization', 'category')
    qs = qs.defer('embedding', 'search_vector', 'raw_data').annotate(
        explore_priority=Case(When(title_query, then=Value(2)), When(body_query, then=Value(1)),
                              default=Value(0), output_field=IntegerField()))
    candidates = load_original_sources(qs.order_by('-explore_priority', '-posted_at', '-pk')[:96])
    ranked = []
    for job in candidates:
        # Keep non-openings and known physical/office roles out of discovery too.
        if not evaluate_job(job, {}, relaxed=True).allowed:
            continue
        decision = evaluate_job(job, preferences, relaxed=True)
        priority = function_priority(job, preferences, terms=terms)
        ranked.append((not decision.allowed, -priority, -job.pk, job, decision))
    reason_text = {
        'location': 'Hiring location differs from your requested location.',
        'excluded_location': 'Hiring location differs from your requested location.',
        'salary': 'Published pay does not establish your requested pay range.',
        'job_type': 'Employment type differs from your brief.',
        'experience': 'Experience level differs from your brief.',
        'experience_evidence': 'Required experience differs from your brief.',
        'required_skill': 'A requested skill is not established by the listing.',
        'work_authorization': 'Work-authorization requirements differ from your brief.',
    }
    picked, seen = [], set()
    for _, _, _, job, decision in sorted(ranked, key=lambda row: row[:3]):
        identity = JobScoutService._repost_key(job)
        if identity in seen:
            continue
        notes = []
        if not decision.allowed:
            notes.append(reason_text.get(decision.reason, 'Role or requirements differ from your brief.'))
        notes.extend(CAVEAT_TEXT.get(code, 'Some requested details need confirmation.') for code in decision.caveats)
        warning = location_evidence_warning(job, {}, '')
        if warning:
            notes.append(warning)
        elif (preferences.get('locations') and location_compatibility(job, preferences['locations']) is not True
                and not {'location_unconfirmed', 'review_location'} & set(decision.caveats)):
            notes.append('Confirm the employer can hire from your working location.')
        pay = preferences.get('salary_requirement')
        if pay and not salary_compatible(job, pay):
            caveat = salary_caveat(job, pay)
            notes.append(CAVEAT_TEXT.get(caveat, 'Published pay differs from your requested pay range.'))
        job.explore_evidence = duty_evidence(job, preferences) or ''
        job.explore_notes = list(dict.fromkeys(notes))[:6]
        job.explore_pay = compensation_display(job)
        picked.append(job)
        seen.add(identity)
        if len(picked) >= min(limit, 3):
            break
    return picked


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
    from jobs.services.scout_source_evidence import load_original_sources
    for job in load_original_sources(qs[:60]):
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
