"""Conservative source evidence used by ranking and compensation displays."""
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache


def source_value(job, annotation, *path):
    if hasattr(job, annotation):
        return getattr(job, annotation)
    value = getattr(job, 'raw_data', {}) or {}
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def source_location(job):
    return source_value(job, 'scout_source_location', 'api_response', 'location', 'name') or ''


def compensation(job):
    """Return original pay units when supplied; never assume full-time hours."""
    review = source_value(job, 'scout_compensation_review', 'compensation_source_review')
    if isinstance(review, dict) and review.get('version') == 1 and review.get('action') == 'suppress':
        return None, None, 'year'
    period = source_value(job, 'scout_source_period', 'salaryPeriod')
    low, high = job.salary_min, job.salary_max
    original_low = source_value(job, 'scout_source_pay_min', 'salaryMinimum')
    original_high = source_value(job, 'scout_source_pay_max', 'salaryMaximum')
    if period and (original_low is not None or original_high is not None
                   or str(period).lower() not in {'year', 'annual', 'annually', 'yearly'}):
        low, high = original_low, original_high
    else:
        period = source_value(job, 'scout_salary_period', 'salary_period') or 'year'
        # Legacy numeric columns are annual-normalized. A legacy unit marker
        # alone cannot turn them into original monthly/hourly amounts.
        if str(period).lower() not in {'year', 'annual', 'annually', 'yearly'}:
            low = high = None
    def number(value):
        try:
            n = Decimal(str(value))
            return n if n.is_finite() and n >= 0 else None
        except (InvalidOperation, ValueError, TypeError):
            return None
    parsed_low, parsed_high = number(low), number(high)
    if low is not None and parsed_low is None or high is not None and parsed_high is None:
        parsed_low = parsed_high = None
    return parsed_low, parsed_high, str(period).lower()


def compensation_currency(job):
    review = source_value(job, 'scout_compensation_review', 'compensation_source_review')
    if isinstance(review, dict) and review.get('version') == 1 and review.get('action') == 'suppress':
        return ''
    # Keep the currency paired with the amount selected by compensation(). A
    # feed's default currency must not override a separately extracted amount.
    period = source_value(job, 'scout_source_period', 'salaryPeriod')
    raw_low = source_value(job, 'scout_source_pay_min', 'salaryMinimum')
    raw_high = source_value(job, 'scout_source_pay_max', 'salaryMaximum')
    uses_source_amount = bool(period and (raw_low is not None or raw_high is not None
        or str(period).lower() not in {'year', 'annual', 'annually', 'yearly'}))
    source_currency = source_value(job, 'scout_source_currency', 'salaryCurrency')
    stored_currency = getattr(job, 'salary_currency', '')
    return str((source_currency or stored_currency if uses_source_amount else stored_currency) or '').upper()


def compensation_display(job):
    review = source_value(job, 'scout_compensation_review', 'compensation_source_review')
    if isinstance(review, dict) and review.get('version') == 1 and review.get('action') == 'suppress':
        return ''
    from django.utils.html import strip_tags
    from jobs.services.term_compensation import quoted_term_compensation
    term = quoted_term_compensation(strip_tags(getattr(job, 'description', '') or ''))
    if term:
        low, high = Decimal(term['minimum']), Decimal(term['maximum'])
        return (f'${low:,.0f} – ${high:,.0f} for {term["duration_value"]} '
                f'{term["duration_unit"]}' + ('s' if term['duration_value'] != 1 else ''))
    low, high, period = compensation(job)
    if low is None and high is None:
        return ''
    currency = compensation_currency(job)
    symbol = {'USD': '$', 'GBP': '£', 'EUR': '€'}.get(currency, currency + ' ' if currency else '')
    def display(n):
        return symbol + (f'{n:,.2f}'.rstrip('0').rstrip('.') if n % 1 else f'{n:,.0f}')
    value = (display(low) + ' – ' + display(high) if low is not None and high is not None
             else display(low) + '+' if low is not None else 'Up to ' + display(high))
    unit = {'annual': 'year', 'annually': 'year', 'yearly': 'year', 'hr': 'hour', 'hourly': 'hour', 'monthly': 'month'}.get(period, period)
    return value + ' per ' + unit + ('' if currency else ' (currency unconfirmed)')


YEAR_REQUIREMENT = re.compile(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:\+|[-–]\s*\d+)?\s+years?\s+(?:of\s+)?(?:[a-z][a-z,&/–-]*\s+){0,8}experience\b",re.I)
YEAR_WORDS = {word:i for i,word in enumerate(['one','two','three','four','five','six','seven','eight','nine','ten'],1)}


def experience_conflict(job, prefs, text):
    levels = set(prefs.get('experience_levels') or [])
    entry = bool(levels) and levels <= {'entry','internship'}
    years = prefs.get('candidate_years')
    if not entry and years is None:return False
    if entry and re.search(r'\b(?:senior|principal|staff|director|head of|chief)\b',job.title,re.I):return True
    ceiling=years if years is not None else 2
    for m in YEAR_REQUIREMENT.finditer(text):
        clause=text[max(0,m.start()-40):m.end()+35]
        if re.search(r'\b(?:our company|our organization|we have|founded|over the last)\b',text[max(0,m.start()-40):m.start()],re.I):continue
        if re.search(r'preferred|bonus|nice to have|desirable',clause,re.I):continue
        value=YEAR_WORDS.get(m[1].lower())
        if value is None:value=int(m[1])
        if value>ceiling:return True
        # "2+ years" or "at least 2 years" is a floor, so it is already beyond an entry-level brief.
        floor=bool(re.match(r'\s*\+',text[m.end(1):m.end(1)+3])) or re.search(r'\b(?:at least|minimum(?: of)?|min\.?)\s*$',text[max(0,m.start()-20):m.start()],re.I)
        if entry and years is None and value==ceiling and value>=2 and floor:return True
    return False


def role_conflict(job, prefs, text):
    alternatives = prefs.get('requested_role_phrases') or []
    if len(alternatives) > 1:
        # Requested functions are alternatives. Each branch retains the shared
        # employment rules; a communications branch cannot veto research work.
        return all(_role_conflict(job, {**prefs, 'titles':[role], 'intent_text':''}, text)
                   for role in alternatives)
    return _role_conflict(job, prefs, text)


def _role_conflict(job, prefs, text):
    wanted = ' '.join(prefs.get('titles') or [])
    # Function-only requests such as "communications roles" have no role noun.
    headline = re.split(r'[.!?]|\b(?:with|using|background|experience)\b', prefs.get('intent_text', ''), maxsplit=1, flags=re.I)[0]
    wanted = wanted + ' ' + headline
    if re.search(r'\b(?:therapist|psychotherapist|clinical social worker|licensed counselor|psychologist)\b', wanted, re.I) and not re.search(r'\bnon[- ]clinical\b', wanted, re.I):
        if re.search(r'\bnon[- ]clinical\s+(?:role|position|work|support|services?)\b|(?:role|position)\s+(?:is|remains)\s+non[- ]clinical|does not provide (?:therapy|clinical)', text, re.I):
            return True
        if not re.search(r'\b(?:therapist|psychotherapist|clinical social worker|licensed counselor|psychologist)\b', job.title, re.I):
            return True
    if re.search(r'\bpolicy\s+(?:roles?|jobs?|positions?|work|analysts?|advisors?|officers?)\b', wanted, re.I):
        if re.search(r'\b(?:scientist|engineer|developer|technician)\b', job.title, re.I) and not re.search(r'\b(?:policy|advocacy|regulatory)\b', job.title, re.I):
            return True
    if set(prefs.get('job_types') or []) == {'full-time'} and re.search(r'\bconsultants?\b', job.title, re.I) and not re.search(r'\bfull[- ]time\b',text,re.I):
        return True
    from jobs.services.scout_constraints import positive_text
    product_wanted=positive_text(wanted)
    if re.search(r'\bproduct\s+(?:management|managers?|owners?|strategy)\b',product_wanted,re.I) and not re.search(r'\b(?:marketing|sales)\b',product_wanted,re.I):
        if re.search(r'\bproduct\s+(?:marketing|sales)\b',job.title,re.I):return True
    if re.search(r'\bcommunications?\b', wanted, re.I):
        functions = r'communications?|media relations|public relations|press|editorial|content|copywriter'
        if re.search(r'\bmarketing\b', wanted, re.I):functions += '|marketing|brand'
        if not re.search(r'\b(?:' + functions + r')\b', job.title, re.I):
            return True
    return False


@lru_cache(maxsize=512)
def _nonprofit_identity_pattern(name):
    subjects=[r'\bwe',r'\bour (?:organization|organisation)']
    if name:subjects.append(r'(?<!\w)'+re.escape(name))
    return re.compile(r'(?:'+ '|'.join(subjects)+r')\s+(?:is|are)\s+(?:an?\s+)?(?:(?:registered|national|international|independent|charitable)\s+){0,3}(?:non[- ]?profit\b|registered charity\b)',re.I)


def nonprofit_compatibility(job, text):
    org=getattr(job,'organization',None)
    kind=getattr(org,'organization_type','')
    if kind in {'nonprofit','foundation'}:return True
    if kind in {'bcorp','social_enterprise','government'}:return False
    from django.utils.html import strip_tags
    from html import unescape
    description=unescape(strip_tags(str(getattr(org,'description','') or '')))
    evidence=' '.join(' '.join((description,unescape(strip_tags(text)))).split())
    name=unescape(str(getattr(org,'name','') or '')).strip()
    if re.search(r'non[- ]?profit|registered charity',evidence,re.I) and _nonprofit_identity_pattern(name).search(evidence):return True
    if re.search(r'\bagency\b.{0,250}\b(?:non[- ]?profit\s+clients|partners with)\b|\bfor[- ]profit\s+(?:company|organization|corporation)\b',text,re.I):return False
    return None


def experience_evidence_warning(job, prefs, text):
    """A requested sector is not evidence of the applicant's experience in it."""
    intent=str(prefs.get('intent_text') or '')
    levels=set(prefs.get('experience_levels') or [])
    years=prefs.get('candidate_years')
    early=bool(levels) and levels <= {'entry','internship'}
    if not (early or years is not None and years <= 2 or re.search(r'\bcareer[- ]changer\b',intent,re.I)):
        return None
    from django.utils.html import strip_tags
    clean=strip_tags(re.sub(r'</(?:p|li|div)>|<br\s*/?>','\n',text,flags=re.I))
    required=False
    for clause in re.split(r'[.!?;\n]',clean):
        if re.search(r'preferred|nice to have|a plus|desirable|not required|no .{0,30}experience required',clause,re.I):continue
        domain_experience=bool(re.search(r'\bnon[- ]?profit\b.{0,60}\bexperience\b|\bexperience\b.{0,90}\bnon[- ]?profit\b',clause,re.I))
        if domain_experience and re.search(r'\brequired\b|\brequirements?\b|\bmust\b|\bwill not be considered\b|\bnot eligible\b',clause,re.I):required=True
    # A heading can be a separate source paragraph from its requirement.
    for match in re.finditer(r'\brequired qualifications?\b.{0,180}\bexperience\b.{0,90}\bnon[- ]?profit\b',clean,re.I|re.S):
        if not re.search(r'preferred|nice to have|a plus|desirable|not required',clean[match.start():match.end()+60],re.I):required=True
    if not required:return None
    stated=False
    for clause in re.split(r'[.!?;\n]',intent):
        if re.search(r'\b(?:no|without|lack|lacking)\b.{0,60}\bnon[- ]?profit\b|\b(?:do not|don.t) have\b.{0,60}\bnon[- ]?profit\b',clause,re.I):continue
        if re.search(r'\b(?:have|bring|possess)\b.{0,60}\bnon[- ]?profit\b.{0,60}\bexperience\b|\b(?:years? of|background in|worked (?:for|with|in)|experience (?:in|with|supporting))\s+(?:\w+\s+){0,5}non[- ]?profits?\b|\bexperienced\s+non[- ]?profit\s+(?:administrator|professional|coordinator)\b',clause,re.I):stated=True
    if stated:return None
    if re.search(r'\breferences?\b.{0,100}\bnon[- ]?profit\b|\bnon[- ]?profit\b.{0,80}\breferences?\b',clean,re.I):
        return 'Required nonprofit experience and references need confirmation'
    return 'Required nonprofit experience is not established by your brief'


def location_evidence_warning(job, prefs, text):
    """Keep source restrictions visible when no applicant location is given."""
    if prefs.get('locations'):return None
    from django_countries import countries
    from jobs.services.scout_source_profile import profile_for
    _, profile = profile_for(job, geography=True)
    facts = profile['geography']
    if text:
        # Callers may supply source text before attaching it to the job object.
        from jobs.services.scout_constraints import extract_location_evidence
        from jobs.services.scout_source_profile import source_fields
        fields = source_fields(job)
        fields.update(description=text, requirements='')
        facts = extract_location_evidence(fields)
    states = facts['offered_states']
    if states:
        return ', '.join(sorted(state.title() for state in states)) + ' residency required; confirm eligibility'
    offered = facts['offered']
    if offered:
        from jobs.services.scout_constraints import REGIONS
        region = next((name for name, codes in REGIONS.items() if set(offered) == codes), None)
        if region:
            return 'Location restricted to ' + region.title() + '; confirm eligibility'
        names = {'US': 'United States', 'GB': 'United Kingdom'}
        labels = sorted(names.get(code, dict(countries).get(code, code)) for code in offered)
        return 'Location restricted to ' + ', '.join(labels) + '; confirm eligibility'
    if facts['denied']:
        labels = sorted(dict(countries).get(code, code) for code in facts['denied'])
        return 'Hiring excludes ' + ', '.join(labels) + '; confirm eligibility'
    return None


def actual_pay_conflict(job, prefs, text):
    if prefs.get('part_time_contract'):
        if job.job_type != 'part-time' and not re.search(r'\bpart[- ]time\b|\b(?:[1-2]?\d|3[0-4])(?:[-–]\d+)?\s+hours?\s+(?:per|a|/)\s*week\b',text,re.I):return True
        if job.job_type not in {'contract','freelance'} and not re.search(r'\b(?:interim|contract|contractor|consulting engagement)\b',text,re.I):return True
    if not prefs.get('actual_annual_pay'):return False
    if re.search(r'\bfull[- ]time[- ]equivalent\b|\bFTE\b',text,re.I):return True
    period=str(source_value(job,'scout_source_period','salaryPeriod') or 'year').lower()
    # Monthly compensation is an annualized rate. A limited engagement or an
    # unspecified term cannot establish actual annual earnings.
    if period in {'month','monthly'} and not re.search(r'\b(?:12[- ]month|one[- ]year|1[- ]year|annual)\s+(?:contract|engagement|term|position|appointment)\b',text,re.I):return True
    return False
