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
    period = source_value(job, 'scout_source_period', 'salaryPeriod')
    low, high = job.salary_min, job.salary_max
    if period and str(period).lower() not in {'year', 'annual', 'annually', 'yearly'}:
        low = source_value(job, 'scout_source_pay_min', 'salaryMinimum')
        high = source_value(job, 'scout_source_pay_max', 'salaryMaximum')
    else:
        period = source_value(job, 'scout_salary_period', 'salary_period') or 'year'
    def number(value):
        try:
            n = Decimal(str(value))
            return n if n.is_finite() and n >= 0 else None
        except (InvalidOperation, ValueError, TypeError):
            return None
    return number(low), number(high), str(period).lower()


def compensation_display(job):
    low, high, period = compensation(job)
    if low is None and high is None:
        return ''
    currency = getattr(job, 'salary_currency', '') or 'USD'
    symbol = {'USD': '$', 'GBP': '£', 'EUR': '€'}.get(currency, currency + ' ')
    def display(n):
        return symbol + (f'{n:,.2f}'.rstrip('0').rstrip('.') if n % 1 else f'{n:,.0f}')
    value = (display(low) + ' – ' + display(high) if low is not None and high is not None
             else display(low) + '+' if low is not None else 'Up to ' + display(high))
    unit = {'annual': 'year', 'annually': 'year', 'yearly': 'year', 'hr': 'hour', 'hourly': 'hour', 'monthly': 'month'}.get(period, period)
    return value + ' per ' + unit


YEAR_REQUIREMENT = re.compile(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:\+|[-–]\s*\d+)?\s+years?\s+(?:of\s+)?(?:[a-z][a-z,&/–-]*\s+){0,8}experience\b",re.I)
YEAR_WORDS = {word:i for i,word in enumerate(['one','two','three','four','five','six','seven','eight','nine','ten'],1)}


def experience_conflict(job, prefs, text):
    levels = set(prefs.get('experience_levels') or [])
    entry = bool(levels) and levels <= {'entry','internship'}
    years = prefs.get('candidate_years')
    if not entry and years is None:return False
    if entry and re.search(r'\b(?:senior|principal|staff|director|head of|chief)\b',job.title,re.I):return True
    ceiling=min(2,years) if entry and years is not None else (2 if entry else years)
    for m in YEAR_REQUIREMENT.finditer(text):
        clause=text[max(0,m.start()-40):m.end()+35]
        if re.search(r'\b(?:our company|our organization|we have|founded|over the last)\b',text[max(0,m.start()-40):m.start()],re.I):continue
        if re.search(r'preferred|bonus|nice to have|desirable',clause,re.I):continue
        value=YEAR_WORDS.get(m[1].lower())
        if value is None:value=int(m[1])
        if value>ceiling:return True
        # "2+ years" or "at least 2 years" is a floor, so it is already beyond an entry-level brief.
        floor=bool(re.match(r'\s*\+',text[m.end(1):m.end(1)+3])) or re.search(r'\b(?:at least|minimum(?: of)?|min\.?)\s*$',text[max(0,m.start()-20):m.start()],re.I)
        if entry and value==ceiling and value>=2 and floor:return True
    return False


def role_conflict(job, prefs, text):
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
    if 'full-time' in (prefs.get('job_types') or []) and re.search(r'\bconsultants?\b', job.title, re.I) and not re.search(r'\bfull[- ]time\b',text,re.I):
        return True
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
    from django.utils.html import strip_tags
    from jobs.services.scout_constraints import state_names,places
    clean=re.sub(r'\bU\.S\.(?:A\.)?', 'USA', strip_tags(text), flags=re.I)
    names={'us':'United States','usa':'United States','united states':'United States','uk':'United Kingdom'}
    def country_warning(offered):
        return 'Location restricted to '+', '.join(sorted({names.get(p,p.title()) for p in offered}))+'; confirm eligibility'
    for clause in re.split(r'[.!?;\n]|\bbut\b',clean,flags=re.I):
        if re.search(r'\b(?:no need to|not required to|do not need to|need not|don.t need to)\b',clause,re.I):continue
        if not re.search(r'\b(?:must|need to|only)\s+(?:be\s+)?(?:reside|based|located)|\bresidency required\b|\bremote\s+(?:within|from|in)\b|\b(?:authorized|eligible) to work from\b',clause,re.I):continue
        states=state_names(clause)
        if states:return ', '.join(sorted(s.title() for s in states))+' residency required; confirm eligibility'
        offered=places(clause)
        if offered:return country_warning(offered)
    location=source_location(job) or str(getattr(job,'location','') or '')
    if re.search(r'\bworldwide|\banywhere|\bglobally',location,re.I):return None
    offered=places(location)
    if offered:
        return country_warning(offered)
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
