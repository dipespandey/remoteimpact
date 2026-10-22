"""Recorded checkout campaign labels; these labels do not prove causality."""
from django.db import transaction
from jobs.models import Job

KEYS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'source')
MODEL = 'checkout_campaign_v1'
SNAPSHOT_KEY = 'employer_checkout_campaign'


def clean_labels(value):
    if not isinstance(value, dict):
        return {}
    return {key: ' '.join(value[key].split())[:100] for key in KEYS
            if isinstance(value.get(key), str) and value[key].strip()}


def request_labels(request):
    return clean_labels(request.session.get('acquisition_attribution', {}))


def stripe_labels(value):
    return {'acquisition_model': MODEL,
            **{'acquisition_' + key: label for key, label in clean_labels(value).items()}}


def payment_labels(metadata):
    if not metadata or metadata.get('acquisition_model') != MODEL:
        return {}
    labels = clean_labels({key: metadata.get('acquisition_' + key) for key in KEYS})
    return {'attribution_model': MODEL, 'campaign_labels': labels}


@transaction.atomic
def posting_labels(job, incoming):
    """Freeze the first checkout touch so retries keep identical Stripe arguments.

    Existing sessions without this snapshot retain their original metadata. Never
    backfill today's campaign into an earlier checkout or change its idempotency.
    """
    current = Job.objects.select_for_update().get(pk=job.pk)
    raw = dict(current.raw_data) if isinstance(current.raw_data, dict) else {}
    snapshot = raw.get(SNAPSHOT_KEY)
    if isinstance(snapshot, dict) and snapshot.get('version') == 1:
        return stripe_labels(snapshot.get('labels'))
    if current.stripe_checkout_session_id:
        return {}
    raw[SNAPSHOT_KEY] = {'version': 1, 'labels': clean_labels(incoming)}
    Job.objects.filter(pk=current.pk).update(raw_data=raw)
    return stripe_labels(incoming)
