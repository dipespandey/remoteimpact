"""Persistent submission acknowledgements; these never assert search indexing."""
import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from jobs.models import Job


def candidates(provider, hours):
    now = timezone.now()
    since = now - timedelta(hours=hours)
    recent = (Q(updated_at__gte=since) | Q(posted_at__gte=since) | Q(last_seen_at__gte=since)
              | Q(expires_at__gte=since, expires_at__lte=now))
    # Source sightings can age out without any row being saved. Notify closure
    # once for URLs previously acknowledged as available by this provider.
    closed = Q(**{f'indexing_state__{provider}__type': 'URL_UPDATED'}) & ~Job.availability_filter(now=now)
    return Job.objects.filter(recent | closed).select_related('organization').defer('embedding', 'search_vector')


def fingerprint(job):
    fields = ('slug', 'title', 'description', 'requirements', 'impact', 'benefits', 'location', 'country',
              'job_type', 'experience_level', 'education_level', 'salary_min', 'salary_max',
              'salary_currency', 'skills', 'expires_at', 'posted_at', 'source', 'application_url')
    body = {field: getattr(job, field) for field in fields}
    body.update(available=job.is_available, organization=job.organization.name)
    from jobs.services.seo_text import job_title_tag
    body['title_tag'] = job_title_tag(job.title, job.organization.name)
    if job.is_available:
        from django.conf import settings
        from jobs.services.job_quality import google_job_posting_json
        body['public_schema'] = google_job_posting_json(job, settings.SITE_URL)
    # Feed sightings and embeddings do not represent a visible-content edit.
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def pending(job, provider):
    state = (job.indexing_state or {}).get(provider, {})
    if not job.is_available:
        # Never send an unpublished draft's URL as a "removal". Only close a
        # URL this provider has already acknowledged as public in this ledger.
        return state.get('type') == 'URL_UPDATED'
    return state.get('fingerprint') != fingerprint(job)


def acknowledge(job, provider, signature):
    """Persist only after an accepted provider response, preserving other providers."""
    with transaction.atomic():
        current = Job.objects.select_for_update().only('indexing_state').get(pk=job.pk)
        state = dict(current.indexing_state or {})
        state[provider] = {'fingerprint': signature, 'type': 'URL_UPDATED' if job.is_available else 'URL_DELETED',
                           'submitted_at': timezone.now().isoformat()}
        # This metadata does not change page freshness or invoke save signals.
        Job.objects.filter(pk=job.pk).update(indexing_state=state)
