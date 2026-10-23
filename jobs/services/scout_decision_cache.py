"""Shared cache-key and retention helpers for verified Scout decisions."""
import hashlib
import json

from django.conf import settings


def decision_key(namespace, *parts):
    """Hash every decision input so raw briefs or job evidence never enter keys."""
    value = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return f'{namespace}:{hashlib.sha256(value.encode()).hexdigest()}'


def decision_ttl():
    """Return the configured lifetime shared by all expensive Scout decisions."""
    return getattr(settings, 'SCOUT_DECISION_CACHE_TTL', 86400 * 7)
