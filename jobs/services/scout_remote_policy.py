"""Final, bounded remote-policy check shared by preview and saved Scouts.

Only a single explicit requested country is supported. Missing applicant facts
stay unknown. Rules run for every selected opening; one optional API batch uses
the existing Scout deadline/call budget. This never adds or promotes a match.
"""
import json
import logging
import re

from django.conf import settings
from django.core.cache import caches

from ml.remote_policy.core import Candidate, NAMES, clean, rules, worldwide_permission, place_codes
from ml.remote_policy.llm import SYSTEM, PROMPT_VERSION, source_payload, decode_decision
from ml.remote_policy.pipeline import combine, should_fallback
from jobs.services.scout_evidence import source_location
from jobs.services.scout_decision_cache import decision_key, decision_ttl

logger = logging.getLogger(__name__)
cache = caches['scout_decisions']
VERSION = 'scout-policy-v2'
MAX_BATCH = 6
MAX_PROMPT_CHARS = 24000
CAVEAT = 'remote_policy_unconfirmed'


def requested_candidate(preferences):
    """A work-from country is not evidence of authorization, state or hours."""
    locations = [v for v in preferences.get('locations') or []
                 if str(v).strip().lower() not in {'worldwide','global','globally','anywhere'}]
    if len(locations) != 1:
        return None
    name = str(locations[0]).strip()
    code = NAMES.get(name.lower())
    if not code and name.upper() in {c for c in NAMES.values()}:
        code = name.upper()
    if not code or name.lower() == 'georgia':
        return None
    return Candidate(code)


def _cached(key, segments):
    try:
        content = cache.get(key)
        if isinstance(content, str):
            return decode_decision(content, segments, 'deepseek-cache')
    except Exception:
        pass
    return None


def check_matches(jobs, preferences, diagnostics, *, allow_api=True):
    jobs = list(jobs)
    if not getattr(settings, 'SCOUT_REMOTE_POLICY_ENABLED', False):
        diagnostics['remote_policy'] = 'disabled'
        return jobs
    candidate = requested_candidate(preferences)
    if candidate is None:
        diagnostics['remote_policy'] = 'unsupported_location_request'
        return jobs
    records = []
    pending = []
    model = str(getattr(settings, 'DEEPSEEK_MODEL', ''))
    diagnostics['remote_policy'] = VERSION
    diagnostics['remote_policy_cache_hits'] = 0
    for index, job in enumerate(jobs):
        text = clean('\n'.join(str(getattr(job, field, '') or '')
                               for field in ('title', 'description', 'requirements')))
        location = clean(source_location(job) or getattr(job, 'location', '') or '')
        decision = rules(text, candidate, location=location)
        record = {'job': job, 'decision': decision, 'text': text}
        records.append(record)
        # Never silently truncate a source to obtain a definitive decision.
        if not should_fallback(decision, candidate, text) or len(text) + len(location) > 16000:
            continue
        segments, payload = source_payload(text, candidate, location)
        key = decision_key('scout-policy-v3', VERSION, PROMPT_VERSION, SYSTEM, model, payload)
        cached = _cached(key, segments)
        if cached is not None:
            record['decision'] = combine(decision, cached, candidate, text)
            diagnostics['remote_policy_cache_hits'] += 1
            continue
        row = {'id': index, **payload}
        if len(pending) >= MAX_BATCH or len(json.dumps([r['row'] for r in pending] + [row])) > MAX_PROMPT_CHARS - 2000:
            continue
        pending.append({'row': row, 'record': record, 'segments': segments, 'key': key})

    if allow_api and pending and getattr(settings, 'DEEPSEEK_API_KEY', ''):
        from jobs.services.scout_workflow import can_call, generate
        if can_call():
            try:
                output = generate(json.dumps({'candidates': [r['row'] for r in pending]}),
                    system_message=SYSTEM + '\nEvaluate each candidate independently. Return JSON only: '
                        '{"decisions":[{"id":0,"label":"unknown","evidence_ids":[]}]}. '
                        'Copy each supplied candidate ID exactly once.',
                    max_tokens=700, temperature=0, timeout=3, stage='remote_policy')
                value = json.loads(output)
                rows = value.get('decisions') if isinstance(value, dict) else None
                wanted = {r['row']['id'] for r in pending}
                if not isinstance(rows, list) or len(rows) != len(wanted):
                    raise ValueError('Missing policy decisions')
                if any(not isinstance(row, dict) or type(row.get('id')) is not int for row in rows):
                    raise ValueError('Invalid policy IDs')
                if {row['id'] for row in rows} != wanted:
                    raise ValueError('Duplicate or unexpected policy IDs')
                by_id = {row['id']: row for row in rows}
                for item in pending:
                    content = json.dumps(by_id[item['row']['id']])
                    result = decode_decision(content, item['segments'], 'deepseek')
                    record = item['record']
                    record['decision'] = combine(record['decision'], result, candidate, record['text'])
                    # Failed/ungrounded outputs should be retried on the next run.
                    if result.reasons:
                        continue
                    try:
                        cache.set(item['key'], content, decision_ttl())
                    except Exception:
                        pass
                diagnostics['remote_policy_api'] = 'reviewed'
            except Exception as exc:
                diagnostics['remote_policy_api'] = 'unavailable'
                logger.warning('Scout remote-policy fallback unavailable (%s)', type(exc).__name__)
        else:
            diagnostics['remote_policy_api'] = 'budget_exhausted'

    kept = []
    counts = {'compatible': 0, 'incompatible': 0, 'unknown': 0}
    for record in records:
        job, decision = record['job'], record['decision']
        job.scout_remote_policy = decision.to_dict()
        counts[decision.label] += 1
        if decision.label == 'incompatible':
            continue
        # Prefer an explicitly compatible home-country policy. Worldwide
        # openings remain useful possibilities even without naming that country.
        # Keep authorization, timezone and other unresolved checks visible.
        country_specific = any(candidate.country in place_codes(e.get('quote',''))
            and e.get('condition') == 'Stated country or regional restriction.'
            and not re.search(r'\b(?:except|excluding|not|cannot)\b', e.get('quote',''), re.I)
            for e in decision.evidence)
        global_policy = worldwide_permission(clean(source_location(job) or getattr(job,'location','') or '')) or any(
            worldwide_permission(e.get('quote','')) for e in decision.evidence)
        job.scout_location_priority = 0 if country_specific and decision.label == 'compatible' else (1 if global_policy else 2)
        if global_policy and not country_specific:
            job.scout_caveats = list(dict.fromkeys([*getattr(job,'scout_caveats',[]), 'worldwide_location_fallback']))
            job.scout_needs_confirmation = True
        if decision.label == 'unknown':
            job.scout_caveats = list(dict.fromkeys([*getattr(job, 'scout_caveats', []), CAVEAT]))
            job.scout_needs_confirmation = True
        kept.append(job)
    diagnostics['remote_policy_counts'] = counts
    return kept
