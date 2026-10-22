"""Preserve reviewed, still-grounded facts across regional feed duplicates."""
import copy
from django.conf import settings
from jobs.services.job_facts import VERSION, plain, source_hash, source_description, reusable_facts
from jobs.services.extraction_contract import CONTRACT_HASH
from jobs.services.extraction_cache import cache_key
from jobs.services.location_normalizer import normalize_location


def retained_facts(job, payload):
    facts = (job.raw_data or {}).get('job_facts')
    if not isinstance(facts, dict) or facts.get('version') != VERSION:
        return None
    title, org = payload.get('title', ''), payload.get('organization_name', '')
    description = source_description(payload)
    digest = source_hash(title, org, description)
    if facts.get('source_hash') != digest:
        if (plain(job.title) != plain(title) or plain(job.organization.name) != plain(org)
                or plain(job.description) != plain(payload.get('description'))
                or plain(job.requirements) != plain(payload.get('requirements') or payload.get('description'))
                or job.location != normalize_location(payload.get('location', 'Remote'))):
            return None
        # Rebase only a single-country location, not a coarsened region such as EU.
        from jobs.services.scout_constraints import places, REGIONS
        found = places(job.location)
        if any(name in REGIONS for name in found) or not found or len(set().union(*found.values())) != 1:
            return None
    result = copy.deepcopy(facts)
    result['source_hash'] = digest
    if not reusable_facts(result, title, org, description, settings.DEEPSEEK_MODEL, CONTRACT_HASH):
        return None
    if facts.get('source_hash') != digest:
        result['source_revalidation'] = {'previous_hash': facts['source_hash'], 'reason': 'unchanged text and normalized country; all evidence revalidated'}
    result['cache_key'] = cache_key(VERSION + CONTRACT_HASH, result['model'], 'https://api.deepseek.com', digest)
    return result
