"""Retain a recent employer review only for the exact imported source input."""
import hashlib
import json
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime


def location_review_input_hash(payload):
    fields = ('source', 'external_id', 'title', 'organization_name', 'application_url',
              'description', 'requirements', 'location', 'country')
    values = {key: str(payload.get(key) or '') for key in fields}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def retained_location_review(job, payload):
    review = (job.raw_data or {}).get('location_source_review')
    if not isinstance(review, dict) or review.get('version') != 1 or review.get('needs_review'):
        return None
    checked = parse_datetime(str(review.get('checked_at') or ''))
    if not checked or timezone.is_naive(checked):
        return None
    age = timezone.now() - checked
    if age < timedelta() or age > timedelta(days=7):
        return None
    if review.get('source_input_hash') != location_review_input_hash(payload):
        return None
    correction = review.get('correction')
    if not isinstance(correction, dict) or not all(
        isinstance(correction.get(key), str) and correction[key].strip()
        for key in ('location', 'country')
    ):
        return None
    return review
