"""Load original stored source prose for a bounded review pool, without browsing.

Generated catalogue summaries are not allowed to override an original source
body. Transient overrides are never saved to the job record.
"""
PATHS = (
    ('api_response', 'descriptionHtml'),
    ('api_response', 'descriptionPlain'),
    ('api_response', 'content'),
    ('api_response', 'description'),
    ('description',),
)


def original_description(raw):
    for path in PATHS:
        value = raw
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, str) and value.strip():
            return value
    return None


def load_original_sources(jobs):
    """One projected query for deferred JSON, not one full-JSON read per job."""
    from jobs.models import Job
    jobs = list(jobs)
    pending = {}
    for job in jobs:
        if getattr(job, '_scout_source_checked', False):
            continue
        data = getattr(job, '__dict__', {})
        if 'raw_data' in data:
            original = original_description(data['raw_data'])
            if original:
                job._scout_source_fields = {'description': original, 'requirements': ''}
            job._scout_source_checked = True
        elif isinstance(job, Job) and job.pk:
            pending.setdefault(job.pk, []).append(job)
    if pending:
        fields = ['raw_data__' + '__'.join(path) for path in PATHS]
        for row in Job.objects.filter(pk__in=pending).values('pk', *fields):
            original = next((row[field] for field in fields
                if isinstance(row[field], str) and row[field].strip()), None)
            for job in pending[row['pk']]:
                if original:
                    job._scout_source_fields = {'description': original, 'requirements': ''}
                job._scout_source_checked = True
    return jobs
