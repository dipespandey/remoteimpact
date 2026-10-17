"""Aggregate-only, point-in-time measurements; never describe this catalogue as the whole market."""
from collections import Counter
from django.utils import timezone
from jobs.api import serialize
from jobs.services.job_service import JobService


def build_snapshot():
    counts = Counter()
    domains = Counter()
    total = 0
    # One database query fixes membership; all ratios use that same denominator.
    for job in JobService.get_available_jobs().select_related('organization','category').defer('embedding','search_vector').iterator(chunk_size=200):
        total += 1
        _, scope, strength = serialize.geography(job)
        counts['worldwide_source_evidence' if scope == 'worldwide' and strength == 'source_backed' else 'other_or_unknown_scope'] += 1
        if serialize.salary(job):
            counts['publishable_annual_pay'] += 1
        if job.salary_min is not None or job.salary_max is not None:
            counts['stored_pay_amounts'] += 1
        domains[job.category.name if job.category else 'Uncategorized'] += 1
    return {
        'title': 'Remote impact jobs: what the catalogue actually tells us',
        'captured_at': timezone.now().isoformat(), 'total_listings': total,
        'counts': dict(counts), 'domains': [{'name': k, 'count': v} for k,v in domains.most_common()],
        'methodology_version': 'agent-facts-2026-10-17',
        'methodology': [
            'One query of Remote Impact listings passing current availability rules; counts are listings, not unique employers or the entire jobs market.',
            'Worldwide source evidence uses the same conservative geography compatibility rules as the jobs API; country restrictions override broad global labels. It is not a guarantee of legal eligibility.',
            'Publishable annual pay uses the JobPosting salary evidence gate: supported unit/currency and valid bounds. Unknown or unpublishable pay is excluded, not treated as zero.',
            'Stored pay amounts count populated numeric fields only; these may be incomplete or inferred and are not necessarily employer salary disclosures.',
            'Categories are catalogue classifications. No trend or causal claim can be drawn from this single observation.',
            'Rows can change at the source after collection. Recheck the employer listing before applying. No personal applicant data is included.'
        ],
        'source_url': 'https://remoteimpact.org/insights/remote-impact-jobs/',
        'data_url': 'https://remoteimpact.org/static/reports/remote-impact-jobs-2026-10-17.json',
    }
