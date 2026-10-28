"""Comparable advertised pay; use the same source evidence as public job records."""
from collections import defaultdict

from jobs.api.serialize import salary
from jobs.services.job_service import JobService


def benchmark_context():
    rows = []
    jobs = JobService.get_available_jobs().filter(
        salary_currency='USD', salary_min__gt=0, salary_max__lt=1000000,
    ).select_related('organization', 'category').defer('embedding', 'search_vector')
    for job in jobs:
        pay = salary(job)
        if pay and pay['currency'] == 'USD' and pay['min'] is not None and pay['max'] is not None:
            rows.append(job)

    def stats(group):
        count = len(group)
        return {'job_count': count, 'avg_min': sum(j.salary_min for j in group) / count if count else None,
                'avg_max': sum(j.salary_max for j in group) / count if count else None,
                'min_salary': min((j.salary_min for j in group), default=None),
                'max_salary': max((j.salary_max for j in group), default=None)}

    domains, types, levels = defaultdict(list), defaultdict(list), defaultdict(list)
    for job in rows:
        if job.category_id:
            domains[(job.category.name, job.category.slug, job.category.icon)].append(job)
        types[job.job_type].append(job)
        if job.experience_level:
            levels[job.experience_level].append(job)
    by_domain = [dict(stats(group), category__name=key[0], category__slug=key[1], category__icon=key[2])
                 for key, group in domains.items() if len(group) >= 10]
    overall = stats(rows)
    overall['total_jobs'] = len(rows)
    return {'by_domain': sorted(by_domain, key=lambda row: -row['job_count']),
            'by_type': [dict(stats(group), job_type=key) for key, group in types.items()],
            'by_experience': [dict(stats(group), experience_level=key) for key, group in levels.items()],
            'overall': overall, 'total_with_salary': len(rows),
            'top_paying': sorted(rows, key=lambda job: (-job.salary_max, -job.pk))[:10]}
