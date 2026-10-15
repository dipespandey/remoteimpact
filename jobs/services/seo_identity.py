"""Stable public identities for equivalent landing pages and proved duplicate jobs."""
import json

IMPACT_CANONICAL_SLUG = 'remote-social-impact-jobs'
IMPACT_PAGE_ALIASES = frozenset({
    'impact-jobs-remote', 'remote-jobs-with-purpose', 'work-from-home-impact-jobs',
    'remote-jobs-that-help-others', 'work-from-anywhere-impact-jobs',
    'online-jobs-for-social-good', 'jobs-that-make-a-difference-remote',
})


def canonical_job(job):
    """Prefer a live reviewed mapping or the oldest live copy of the same source posting.

    A title or numeric slug suffix alone is insufficient: different requisitions
    and country-specific roles must retain their own canonical URLs.
    """
    from jobs.models import Job, JobSlugRedirect
    from jobs.services.importers.common import _canonical_duplicate_url

    live = Job.objects.filter(Job.availability_filter())
    reviewed = JobSlugRedirect.objects.filter(old_slug=job.slug, job__in=live).select_related('job').first()
    if reviewed and reviewed.job_id != job.pk:
        return reviewed.job
    identity = _canonical_duplicate_url(job.application_url)
    if not identity:
        return job
    copies = live.filter(organization_id=job.organization_id, title__iexact=job.title,
                         country=job.country, location=job.location).only('id', 'slug', 'application_url')
    matching = [candidate for candidate in copies
                if _canonical_duplicate_url(candidate.application_url) == identity]
    return min(matching, key=lambda candidate: candidate.pk, default=job)


def job_detail_identity(job, site_url, *, available):
    from jobs.services.job_quality import google_job_posting_json

    canonical = canonical_job(job) if available else job
    url = site_url.rstrip('/') + canonical.get_absolute_url()
    encoded = google_job_posting_json(job, site_url) if available else ''
    if encoded and canonical.pk != job.pk:
        data = json.loads(encoded)
        data['url'] = url
        data['identifier']['value'] = str(canonical.pk)
        encoded = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return {'job_canonical_url': url, 'job_posting_schema_json': encoded}
