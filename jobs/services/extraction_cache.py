"""Shared extraction cache with recovery from facts already persisted on jobs."""
import hashlib
import json
import logging
from asgiref.sync import sync_to_async
from django.core.cache import caches

logger = logging.getLogger(__name__)
TTL = 30 * 24 * 60 * 60


def cache_key(version, model, endpoint, source_digest):
    digest = hashlib.sha256(json.dumps([version, model, endpoint, source_digest]).encode()).hexdigest()
    return 'job-extraction:' + digest


async def get_facts(key):
    try:
        hit = await caches['job_extraction'].aget(key)
        if isinstance(hit, dict):
            return hit
    except Exception:
        logger.warning('Extraction cache read unavailable')
    try:
        from jobs.models import Job
        hit = await sync_to_async(lambda: Job.objects.filter(
            raw_data__job_facts__cache_key=key,
        ).values_list('raw_data', flat=True).first(), thread_sensitive=True)()
        facts = hit.get('job_facts') if isinstance(hit, dict) else None
        if isinstance(facts, dict):
            await set_facts(key, facts)
            return facts
    except Exception:
        logger.warning('Persisted extraction lookup unavailable')
    return None


async def set_facts(key, facts):
    try:
        await caches['job_extraction'].aset(key, facts, timeout=TTL)
    except Exception:
        logger.warning('Extraction cache write unavailable')
