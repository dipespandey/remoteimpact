"""Bounded source excerpts and per-constraint evidence for Scout reviews.

No generated summary is treated as source evidence. Excerpts remain verbatim so
model decisions can be checked against the exact text supplied to it.
"""
import re
from datetime import timedelta
from django.utils import timezone

PATTERNS = (
    r'\b(?:hybrid|on[- ]site|in[- ]office|remote work|fully remote|work remotely|located in|based in|reside|residents|work authorization|eligible to work)\b',
    r'\b(?:responsibilities|you will|you.ll|duties|this role|this job|customer success|upsell|policy|develop|build|analy[sz])\b',
    r'\b(?:requirements|qualifications|\d+\+?\s*years?|mid[- ]level|junior|internship)\b',
    r'\b(?:salary|compensation|USD|EUR|GBP|per year|annually)\b',
)


def excerpt(text, intent='', limit=2200):
    """Sample duties and constraints throughout the source, not just its intro."""
    if len(text) <= limit:
        return text
    # Round-robin categories so a long duties section cannot hide geography/pay.
    patterns = [*PATTERNS, '|'.join(re.escape(w) for w in re.findall(r'\b[a-z]{4,}\b', intent.lower())) or r'(?!)']
    buckets = [[(max(0, m.start()-100), min(len(text), m.end()+250))
                for m in re.finditer(pattern, text, re.I)][:20] for pattern in patterns]
    spans = []
    for round_ in range(20):
        for bucket in buckets:
            if round_ >= len(bucket):
                continue
            start, end = bucket[round_]
            if any(start < b and end > a for a, b in spans):
                continue
            if sum(b-a for a, b in spans) + end-start + 5*len(spans) <= limit:
                spans.append((start, end))
    if not spans:
        return text[:limit]
    return ' […] '.join(text[a:b] for a, b in sorted(spans))


def workload_allocations(text):
    """Find rare percentage markers first, then inspect a bounded heading."""
    for match in re.finditer(r'\((\d{1,3})%\)',text):
        start=max(0,match.start()-65)
        label=re.search(r'[A-Za-z][A-Za-z &,/-]{3,65}$',text[start:match.start()].rstrip())
        if label:
            yield label[0],int(match[1]),start+label.start(),match.end()


def primary_duties(text, limit=900):
    """Preserve the role overview and explicit workload allocations verbatim."""
    headings=r'(?:position overview|position summary|job summary|about this role|about this team and role|role overview)'
    match=re.search(r'\b'+headings+r'\b\s*[:*]*',text,re.I)
    if match:
        start=match.end()
        stop=re.search(r'\b(?:key responsibilities|responsibilities|essential duties|qualifications|requirements|what you.ll do)\b',text[start:],re.I)
        end=start+stop.start() if stop else len(text)
        overview=text[start:min(end,start+650)]
    else:
        # Short structured descriptions usually lead with the actual work.
        overview=text[:450]
    shares=[]
    for label,share,start,end in workload_allocations(text):
        if 30<=share<=100:shares.append(text[start:min(len(text),end+100)])
    return ' […] '.join([overview,*shares])[:limit]


def dominant_function_conflict(text, prefs, title=""):
    """Use explicit workload allocation, never keyword counts, as negative evidence."""
    wanted=prefs.get('intent_text','')
    if re.search(r'\b(?:COO|chief operating officer)\b',wanted,re.I) and re.search(r'\b(?:COO|chief operating officer)\b',title,re.I):
        return False
    for label,share,start,end in workload_allocations(text):
        if share<50:continue
        if re.search(r'\b(?:finance|financial (?:management|operations)|accounting|bookkeeping)\b',label,re.I) and not re.search(r'\b(?:finance|financial|accounting|bookkeeping|CFO|chief financial)\b',wanted,re.I):
            return True
    return False


def evidence_fields(job, preferences):
    from jobs.services.scout_constraints import plain_job_field
    description = plain_job_field(job, 'description')
    requirements = plain_job_field(job, 'requirements')
    duplicate = requirements == description
    intent = preferences.get('intent_text', '')
    return {'title': plain_job_field(job, 'title'),
            'description': primary_duties(description,700) + ' […] ' + excerpt(description, intent, 2100 if duplicate else 1100),
            'requirements': '' if duplicate else excerpt(requirements, intent, 1000)}


def remote_requested(prefs):
    text = prefs.get('intent_text', '')
    return bool(re.search(r'\bremote\b', text, re.I)) and not re.search(r'\bremote\s+(?:or|and)\s+(?:hybrid|on[- ]site)\b', text, re.I)


def workplace_conflict(job, prefs, text):
    if not remote_requested(prefs):
        return False
    # Require workplace wording: hybrid cloud and remote build caches aren't locations.
    pattern = r'\bhybrid (?:working|work|role|position|model|schedule|arrangement)|\b(?:role|position|work(?:ing)? (?:model|arrangement)) (?:is|offers|requires|will be) (?:a )?hybrid\b|\b(?:on[- ]site|in[- ]office) (?:role|position|work|attendance)\b|\b(?:required to|must|expected to) (?:work|be|attend) (?:in|from|at) (?:the|our|an) office\b'
    for clause in re.split(r'[.!?;\n]', text):
        if re.search(r'remote and hybrid work options', clause, re.I):
            continue
        if re.search(pattern, clause, re.I) and not re.search(r'\b(?:optional|not required|no mandatory|no hybrid|no on[- ]site|or fully remote|or remote|remote and hybrid work options)\b', clause, re.I):
            return True
    return False


def requested_checks(prefs):
    checks = ['function']
    for key, wanted in [('remote', remote_requested(prefs)), ('location', prefs.get('locations')),
                        ('seniority', prefs.get('experience_levels')), ('skills', prefs.get('required_skills')),
                        ('salary', prefs.get('salary_requirement') or prefs.get('min_salary'))]:
        if wanted:
            checks.append(key)
    return checks


CHECK_INSTRUCTIONS = (
    'For each selected job include checks for exactly these requested dimensions: {checks}. '
    'Each checks value is [status, field, verbatim_quote], where status is confirmed, conflicting, or unknown. '
    'Confirmed/conflicting needs an exact 8-180 character quote (short location labels are allowed) from title, description, requirements, location or salary. '
    'Unknown must use ["unknown", "", ""]. Function must be confirmed by actual duties in description/requirements, '
    'never a title, a qualification, or company mission. The description starts with the role overview and explicit workload shares. Compare those first: minor or incidental responsibilities cannot establish the requested profession. Same sector with a different profession is conflicting, not adjacent. '
    'Preserve functional qualifiers: partner operations means onboarding/workflows, not sales, fundraising or relationship development. '
    'Remote means the work arrangement, not remote servers/caches; hybrid office attendance conflicts. '
    'Location must reflect explicit hiring/residence eligibility, not an office address or inferred worldwide default. '
    'For a Europe-wide request a named European country is compatible, but quote its residence restriction. '
    'Judge seniority from duties/experience, not imported labels alone. Skills must be used in the role, not just mentioned. '
    'Salary must meet the requested currency and pay period; undisclosed pay is unknown. '
    'Any conflicting dimension disqualifies a job. Unknown function disqualifies it. '
    'Other unknowns are allowed only as clearly caveated alternatives. Do not invent or infer missing facts. '
)


def review_checks(item, row, prefs):
    """Return caveat keys for a valid selection, or None for unsupported/conflicting."""
    from jobs.services.scout_reranker import _normalized
    if dominant_function_conflict(row.get('primary_duties',row.get('description','')),prefs,row.get('title','')):
        return None
    checks = item.get('checks')
    if not isinstance(checks, dict) or set(checks) != set(requested_checks(prefs)):
        return None
    unknown = []
    for name, value in checks.items():
        if not isinstance(value, list) or len(value) != 3:
            return None
        status, field, quote = value
        if status == 'unknown' and field == quote == '':
            if name == 'function':
                return None
            unknown.append('review_' + name)
            continue
        if status != 'confirmed' or field not in ('title', 'description', 'requirements', 'location', 'salary'):
            return None
        if name == 'function' and isinstance(quote,str) and re.match(r'\s*(?:requires?\b|experience\b|proficiency\b|strong knowledge\b|\d+\+? years)',quote,re.I):
            return None
        if name == 'function' and field not in ('description', 'requirements'):
            return None
        if name == 'function' and re.search(r'\bpartner(?:ship)? operations\b', prefs.get('intent_text', ''), re.I):
            if not isinstance(quote, str) or not re.search(r'\b(?:onboarding|workflows?|toolkits?|operations|enablement|processes)\b', quote, re.I):
                return None
        if name == 'function' and re.search(r'\bpolicy\s+(?:roles?|jobs?|work|analyst|advisor)', prefs.get('intent_text', ''), re.I):
            if not isinstance(quote, str) or not re.search(r'\b(?:policy|policies|advocacy|legislation|government affairs|regulatory analysis)\b', quote, re.I):
                return None
        if not isinstance(quote, str) or not (2 if field == 'location' else 8) <= len(quote) <= 360:
            return None
        if _normalized(quote) not in _normalized(row.get(field, '')):
            return None
        if name == 'skills' and re.search(r'\b(?:useful|preferred|nice to have|bonus)\b', quote, re.I):
            unknown.append('review_skills')
        if name == 'remote' and not (field == 'location' and re.search(r'\bremote\b', quote, re.I)):
            if not re.search(r'\b(?:fully|100%) remote|\bremote (?:role|position|work|job|team)|\bwork(?:ing)? remotely|\bwork from home', quote, re.I):
                unknown.append('review_remote')
        if name == 'location' and not re.search(r'\b(?:remote|located|based|reside|residents|hiring|eligible|work from|work in|anywhere)\b', quote, re.I):
            unknown.append('review_location')
    return unknown


def apply_review(job, item, row, prefs):
    caveats = review_checks(item, row, prefs)
    if caveats is None:
        return False
    existing = list(getattr(job, 'scout_caveats', []))
    aliases = {'review_location': 'location_unconfirmed', 'review_salary': 'salary_unlisted'}
    job.scout_caveats = list(dict.fromkeys([*existing, *[c for c in caveats if aliases.get(c) not in existing]]))
    job.scout_constraint_checks = {name: (['unknown', '', ''] if 'review_' + name in caveats else value)
                                   for name, value in item['checks'].items()}
    reasons = [reason for reason in getattr(job, 'scout_reasons', [])
               if not reason.startswith('Relevant evidence:') and not
               ('review_seniority' in caveats and 'experience level' in reason.lower())]
    job.scout_reasons = ['Relevant evidence: ' + item['checks']['function'][2], *reasons]
    from jobs.services.scout_evidence import location_evidence_warning
    from jobs.services.scout_constraints import plain_job_field
    warning = location_evidence_warning(job, prefs, ' '.join(plain_job_field(job, f) for f in ['description', 'requirements']))
    if warning:
        job.scout_needs_confirmation = True
        job.scout_reasons = list(dict.fromkeys([*job.scout_reasons, warning]))
    # Keep geographic restrictions visible even when the broad region matches.
    location = item['checks'].get('location')
    if location and location[0] == 'confirmed' and 'review_location' not in caveats:
        job.scout_reasons = [*getattr(job, 'scout_reasons', []), 'Location evidence: ' + location[2]]
    return True


def listing_caveats(job):
    # Age is uncertainty, never proof that a vacancy has closed. A feed sighting
    # is not a fresh employer confirmation, so don't use last_seen_at here.
    posted = getattr(job, 'posted_at', None)
    return ['listing_age'] if posted and posted < timezone.now()-timedelta(days=180) else []
