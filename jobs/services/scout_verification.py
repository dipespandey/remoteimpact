"""Deterministic source gates around the bounded Scout review workflow.

Listing text and model verdicts are data, never instructions. No network calls,
provider calls, persisted job edits or messages are performed here.
"""
import re
from urllib.parse import urlsplit

VERSION = 2
HOURS = re.compile(r'(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*(?:[-–]\s*(\d{1,2}(?:\.\d+)?))?\s*(?:max\s*)?(?:hours?|hrs?)\s*(?:/|per\s+|a\s+)\s*week\b', re.I)


def source_non_opening(source, raw_data):
    """Probably Good explicitly distinguishes resources from advertised jobs."""
    if source != 'probablygood' or not isinstance(raw_data, dict):
        return False
    workloads = raw_data.get('tags_workload')
    if not isinstance(workloads, list):
        return False
    return any(isinstance(value, str) and value.strip().casefold() in {
        'training', 'funding',
    } for value in workloads)


def job_source_non_opening(job):
    """Use projected source tags without loading deferred full source JSON."""
    data = getattr(job, '__dict__', {})
    raw = data.get('raw_data')
    raw = raw if isinstance(raw, dict) else {}
    workloads = data.get('scout_source_workloads', raw.get('tags_workload'))
    return source_non_opening(data.get('source', ''), {'tags_workload': workloads})


def non_opening(title, application_url=''):
    """Use identity evidence as well as prose that enrichment may have rewritten."""
    # A complete advising-service title is not an opening for a career adviser.
    # Keep occupational titles such as Career Coach or Advising Manager eligible.
    if re.fullmatch(r'\s*(?:1\s*:\s*1|one[- ]to[- ]one)\s+career\s+(?:guidance|advising|coaching)\s*', str(title or ''), re.I):
        return True
    if re.fullmatch(r'\s*(?:personal\s+)?career\s+advising\s*', str(title or ''), re.I):
        return True
    if re.match(r'^\s*(?:online\s+)?courses?\s*[,：:–—]\s*\S', str(title or ''), re.I):
        return True
    try:
        target = urlsplit(str(application_url or ''))
        host = (target.hostname or '').lower().removeprefix('www.')
        path = target.path.lower()
    except ValueError:
        return False
    return bool(
        host == 'coursera.org' and path.startswith(('/learn/', '/specializations/', '/professional-certificates/'))
        or host == 'edx.org' and path.startswith(('/learn/', '/course/'))
        or host == 'udemy.com' and path.startswith('/course/')
    )


def physical_work(title, text):
    """Concrete physical duties, not incidental travel or software device drivers."""
    if not re.search(r'\b(?:meals? driver|delivery driver|truck driver|bus driver|courier)\b', title, re.I):
        return False
    return bool(re.search(
        r'\b(?:delivers?|transport\w*)\b.{0,90}\b(?:meals?|packages?|passengers?|consumers?|homes?)\b|'
        r'\b(?:own|personal) (?:vehicle|transportation)\b|\bdriv\w*\b.{0,50}\b(?:route|license|licence)\b',
        text, re.I,
    ))


def weekly_hours_requirement(text):
    match = HOURS.search(text)
    if not match:
        return None
    low, high = float(match[1]), float(match[2] or match[1])
    if not 0 < low <= high <= 168:
        return None
    prefix = text[max(0, match.start()-35):match.start()]
    mode = 'target'
    if re.search(r'\b(?:at most|up to|maximum|max|no more than|not exceed)\s*$', prefix, re.I) or re.search(r'max', match[0], re.I):
        mode = 'maximum'
    elif re.search(r'\b(?:at least|minimum|min|no fewer than)\s*$', prefix, re.I):
        mode = 'minimum'
    elif match[2]:
        mode = 'range'
    return {'min': low, 'max': high, 'mode': mode}


def weekly_hours(job):
    from jobs.services.scout_constraints import plain_job_field
    spans = set()
    for field in ('description', 'requirements'):
        for match in HOURS.finditer(plain_job_field(job, field)):
            low, high = float(match[1]), float(match[2] or match[1])
            if 0 < low <= high <= 168:
                spans.add((low, high))
    return next(iter(spans)) if len(spans) == 1 else None


def hours_status(job, requirement):
    """conflict, different, unknown or compatible; never infer full-time hours."""
    if not requirement:
        return 'compatible'
    offered = weekly_hours(job)
    if offered is None:
        return 'unknown'
    low, high = offered
    minimum, maximum, mode = requirement['min'], requirement['max'], requirement['mode']
    if mode in ('maximum', 'range') and low > maximum:
        return 'conflict'
    if mode in ('minimum', 'range') and high < minimum:
        return 'conflict'
    if mode == 'target' and not low <= minimum <= high:
        return 'different'
    if mode in ('maximum', 'range') and high > maximum or mode in ('minimum', 'range') and low < minimum:
        return 'unknown'
    return 'compatible'


def needs_role_details(preferences):
    if not preferences.get('intent_text') or preferences.get('titles') or preferences.get('skills'):
        return False
    generic = set('i im am a an the want wants thats that is its looking look for seeking seek find me '
        'my we our us please any anything something jobs job roles role work working employment position positions '
        'full time part fulltime parttime remote paid stable decent good pay salary compensation '
        'per hour hours hourly week weekly month monthly year yearly annual least minimum maximum '
        'at to from in of with and or need which can be done anywhere world globally worldwide'.split())
    locations = set(re.findall(r'[a-z]+', ' '.join(preferences.get('locations') or []).lower()))
    words = set(re.findall(r'[a-z]+', preferences['intent_text'].lower()))
    return not (words - generic - locations)


def verify_matches(jobs, preferences, diagnostics, *, annotate=True):
    """Model acceptance cannot override a source contradiction or erase caveats."""
    from jobs.services.scout_constraints import evaluate_job
    from jobs.services.scout_evidence import compensation_display
    from jobs.services.scout_source_evidence import load_original_sources
    from jobs.services.scout_reranker import _normalized
    from jobs.services.scout_constraints import plain_job_field
    from jobs.services.scout_review import source_checks_grounded
    kept = []
    rejected = {}
    diagnostics['source_verification_version'] = VERSION
    for job in load_original_sources(jobs):
        decision = evaluate_job(job, preferences, relaxed=True)
        if not decision.allowed:
            rejected[decision.reason] = rejected.get(decision.reason, 0) + 1
            continue
        checks = getattr(job, 'scout_constraint_checks', None)
        if checks and not source_checks_grounded(job, {'checks': checks}):
            rejected['source_evidence'] = rejected.get('source_evidence', 0) + 1
            continue
        if getattr(job, '_scout_source_fields', None):
            source = _normalized(' '.join(plain_job_field(job, f) for f in ('title', 'location', 'description', 'requirements')))
            quotes = [r[len(prefix):] for r in getattr(job, 'scout_reasons', []) if isinstance(r, str)
                      for prefix in ('Relevant evidence: ', 'Listing duties: ') if r.startswith(prefix)]
            if any(_normalized(quote) not in source for quote in quotes):
                rejected['source_evidence'] = rejected.get('source_evidence', 0) + 1
                continue
        if not annotate:
            # Historical display keeps recorded rank/reasons intact. Only a
            # known contradiction hides a suggestion; never rewrite its ledger.
            kept.append(job)
            continue
        job.scout_caveats = list(dict.fromkeys([*getattr(job, 'scout_caveats', []), *decision.caveats]))
        if needs_role_details(preferences):
            job.scout_caveats = list(dict.fromkeys([*job.scout_caveats, 'request_details_needed']))
        reasons = list(getattr(job, 'scout_reasons', []))
        # Apply source restrictions to model-reviewed and recovered rows alike.
        from jobs.services.scout_evidence import location_evidence_warning
        warning = location_evidence_warning(job, preferences, '')
        if warning:
            job.scout_caveats = list(dict.fromkeys([*job.scout_caveats, 'location_required']))
            job.scout_needs_confirmation = True
            reasons.append(warning)
        if preferences.get('salary_requirement'):
            pay = compensation_display(job)
            if pay:
                # Source amounts may be known while comparisons remain unknown.
                # Preserve review_salary, but don't call disclosed pay unlisted.
                job.scout_caveats = [c for c in job.scout_caveats if c != 'salary_unlisted']
                reasons = [r for r in reasons if r != 'Check: salary not listed']
                reasons.append('Listed pay: ' + pay)
        if preferences.get('weekly_hours'):
            hours = weekly_hours(job)
            if hours:
                low, high = hours
                stated = f'{low:g}' if low == high else f'{low:g}–{high:g}'
                reasons.append(f'Listed hours: {stated} hours/week')
        job.scout_reasons = list(dict.fromkeys(reasons))
        kept.append(job)
    diagnostics['source_verification_rejections'] = rejected
    return kept
