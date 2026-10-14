"""Explicit Scout constraints shared by preview, saved Scouts and retrieval.

Source geography is tri-state: confirmed compatible, incompatible or unknown.
No salary conversion or work-authorization inference is performed.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import re

from django_countries import countries
from jobs.services.scout_evidence import source_location, source_value, experience_conflict, role_conflict, actual_pay_conflict, nonprofit_compatibility


EU = set('AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE'.split())
EUROPE = EU | set('AL AD BY BA GB IS LI MD MC ME MK NO RU SM RS CH TR UA VA XK'.split())
AFRICA = set('DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY MG MW ML MR MU MA MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN UG ZM ZW'.split())
ASIA = set('AF AM AZ BH BD BT BN KH CN GE IN ID IR IQ IL JP JO KZ KW KG LA LB MY MV MN MM NP KP OM PK PS PH QA SA SG KR LK SY TW TJ TH TL TM AE UZ VN YE'.split())
LATAM = set('AR BO BR CL CO CR CU DO EC SV GT HT HN MX NI PA PY PE PR UY VE'.split())
REGIONS = {'europe': EUROPE, 'eu': EU, 'eea': EU | {'NO','IS','LI'}, 'africa': AFRICA,
           'asia': ASIA, 'asia pacific': ASIA | {'AU','NZ'}, 'apac': ASIA | {'AU','NZ'},
           'north america': {'US','CA','MX'}, 'latin america': LATAM, 'latam': LATAM,
           'south america': LATAM - {'MX','CR','CU','DO','SV','GT','HT','HN','NI','PA','PR'},
           'emea': EUROPE | AFRICA | {'AE','SA','IL','QA','BH','OM','KW','JO','LB'}}
ALIASES = {'usa':'US','us':'US','u.s.':'US','u.s.a.':'US','united states':'US',
           'uk':'GB','united kingdom':'GB','england':'GB','scotland':'GB','wales':'GB',
           'uae':'AE','united arab emirates':'AE','south korea':'KR','czech republic':'CZ'}
WORLD = {'worldwide','anywhere','global','globally'}
# Full state names avoid confusing ordinary two-letter words with geography.
US_STATES = set('alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming'.split('|'))
STATE_PATTERN = re.compile(r'\b(?:' + '|'.join(sorted(US_STATES, key=len, reverse=True)) + r')\b', re.I)


def state_names(text):
    # Georgia without a US/state qualifier can be the country.
    result={m.group().lower() for m in STATE_PATTERN.finditer(str(text or ''))}
    if 'georgia' in result and not re.search(r'\b(?:US|USA|United States|state of Georgia|Georgia state)\b', str(text), re.I):
        result.remove('georgia')
    return result

NEGATION = r"\b(?:exclude|excluding|not interested in|not looking for|do not want|don't want|avoid|stay away from|must not(?: require)?|mustn't(?: require)?|without|no|not)\s+"
# A clause after "without"/"no" that describes the candidate, not the job.
CANDIDATE_ATTRIBUTE = re.compile(r"^(?:a |an |any |much |formal |prior |previous )*(?:(?:college |university |bachelor'?s? |master'?s? )?degree|experience|certifications?|licen[cs]es?|background|clue|idea)\b", re.I)
# Countries named as past experience or subject matter are not eligibility.
EXPERIENCE_CONTEXT = re.compile(r"(?:worked|working|experience|served|volunteered|projects?|programs?|programmes?|policy|research|studied|study|degree|background|focus(?:ed)? on|speciali[sz]\w* in|knowledge of|expertise in|career in)\b[^.;!?]{0,40}$", re.I)
VERB_FRAGMENT = re.compile(r"^(?:(?:i\s+)?(?:am\s+|'m\s+)?(?:looking for|interested in|want(?:ing)? to|to do|to work (?:in|on|with)|work(?:ing)? (?:in|on|with)|doing|anything (?:in|to do with|related to)|something (?:in|with)|roles? (?:in|with)|jobs? (?:in|with)|positions? (?:in|with)|in|on|with|the|any|a|an)\s+)+", re.I)
BENEFIT_SENTENCE = re.compile(r"\b(?:benefits?|insurance|coverage|covered|eap|employee assistance|wellness|well-being|wellbeing|pto|paid time off|401\(?k|medical,? dental|perks?|stipend)\b", re.I)
DOMAIN_VARIANTS = {
    'behavioral health': ('behavioral health', 'behavioural health'),
    'behavioural health': ('behavioral health', 'behavioural health'),
    'mental health': ('mental health',),
    'public health': ('public health',),
}


@lru_cache(maxsize=1)
def country_terms():
    terms = {str(name).lower(): code for code,name in countries}
    terms.update(ALIASES)
    return terms


@lru_cache(maxsize=1)
def place_patterns():
    members={name:{code} for name,code in country_terms().items()}
    members.update(REGIONS)
    # One longest-first token scan avoids hundreds of regex scans per clause.
    pattern=re.compile(r'(?<![a-z])(?:'+ '|'.join(re.escape(name) for name in sorted(members,key=len,reverse=True)) + r')(?![a-z])')
    return members,pattern


@lru_cache(maxsize=2048)
def places(text, *, codes=False):
    """Country names and region names only; ordinary two-letter words aren't countries."""
    text=str(text or '').lower()
    members,pattern=place_patterns()
    found={match.group():members[match.group()] for match in pattern.finditer(text)}
    if codes:
        value=text.strip().upper()
        if value in countries:found[value.lower()]={value}
    return found


def location_requests(text):
    """Places the seeker asks to work from, not places named as background."""
    lowered=str(text or '').lower()
    members,pattern=place_patterns()
    result=[]
    for match in pattern.finditer(lowered):
        name=match.group()
        if re.match(r'\s*(?:dollars?|currency)\b',lowered[match.end():]):
            continue  # currency denomination is not applicant geography
        if name=='us' and not re.search(r'(?<![A-Za-z])US(?![A-Za-z])',str(text)[match.start():match.end()]):
            continue  # the pronoun "us", e.g. "let us know"
        prefix = lowered[max(0, match.start()-60):match.start()]
        requested_role_location = bool(re.search(r"\b(?:jobs?|roles?|positions?|opportunities)\b[^.;!?]{0,30}\b(?:in|across|within|from)\s*$", prefix))
        historical_context = bool(re.search(r"\b(?:worked|served|volunteered|studied|background|experience)\b[^.;!?]*$", prefix))
        if EXPERIENCE_CONTEXT.search(prefix) and not (requested_role_location and not historical_context):
            continue
        if name not in result:
            result.append(name)
    return result


def domain_evidence(job, domain):
    """Where a requested field appears: 'title', 'body', or None.

    Benefits boilerplate ("behavioral health coverage") is not evidence that the
    job itself is in that field.
    """
    variants=DOMAIN_VARIANTS.get(domain.lower(),(domain,))
    pattern=re.compile(r'(?<!\w)(?:'+'|'.join(re.escape(v) for v in variants)+r')(?!\w)',re.I)
    if pattern.search(plain_job_field(job,'title')) or pattern.search(str(getattr(getattr(job,'organization',None),'name','') or '')):
        return 'title'
    body=' '.join(plain_job_field(job,f) for f in ['description','requirements'])
    for sentence in re.split(r'(?<=[.!?;])\s+|\n',body):
        if pattern.search(sentence) and not BENEFIT_SENTENCE.search(sentence):
            return 'body'
    return None


def expand(values):
    result = set()
    for value in values or []:
        for members in places(value, codes=True).values():
            result |= members
    return result


def negative_clauses(text):
    result = []
    # Each sentence/semicolon may contain multiple negative clauses. Do not
    # swallow the next positive request or lose a second sentence's exclusions.
    markers=list(re.finditer(NEGATION,text,re.I))
    for index,match in enumerate(markers):
        end=markers[index+1].start() if index+1<len(markers) else len(text)
        clause=re.split(r"[.!?;\n—–]|\b(?:but|instead|however|rather|looking for|seeking|I(?:'d| would| want| prefer| am|'m| need| hope))\b",text[match.end():end],maxsplit=1,flags=re.I)[0]
        # "without a degree" describes the seeker; it is not an excluded job term.
        if CANDIDATE_ATTRIBUTE.match(clause.strip()) and re.match(r'(?:without|no)\b',match.group(),re.I):
            continue
        clause=re.sub(r'\s+and\s*$','',clause,flags=re.I)
        result.append((match.start(),match.end()+len(clause),clause.strip()))
    return result


def positive_text(text):
    for start,end,_ in reversed(negative_clauses(text)):
        text = text[:start] + ' ' + text[end:]
    # Non-clinical isn't a request for clinical roles.
    return text


def canonical_exclusion(value):
    value = VERB_FRAGMENT.sub('', value.lower().strip(' .,-'))
    value = re.sub(r'\b(?:jobs?|roles?|positions?)\b', '', value).strip(' .,-')
    value = re.sub(r'^(?:that are |that involve |involving )?(?:heavy on |in |with )?', '', value)
    return {'internships':'internship','interns':'intern','fellowships':'fellowship'}.get(value,value)


def extract_constraints(text):
    text = ' '.join(str(text or '').split())[:2000]
    negatives = negative_clauses(text)
    positive = positive_text(text)
    exclusions, denied_places, denied_auth = [],[],[]
    candidate_years=None
    count=re.search(r'\b(\d{1,2}|one|two|three|four|five)\s+years?\s+of\s+(?:[a-z]+\s+){0,4}experience\b',positive,re.I)
    if count and not re.search(r'\b(?:at least|minimum|requiring|required)\b',positive[max(0,count.start()-25):count.start()],re.I):
        candidate_years={'one':1,'two':2,'three':3,'four':4,'five':5}.get(count[1].lower())
        if candidate_years is None:candidate_years=int(count[1])
    for _,_,clause in negatives:
        for part in re.split(r',|\bor\b|\band\b', clause, flags=re.I):
            part = part.strip(' .')
            if not part:
                continue
            found = places(part)
            if found and (part.lower() in country_terms() or part.lower() in REGIONS or re.search(r'only|based|work|authoriz|visa|citizen|resid|remote|jobs', part, re.I)):
                if re.search(r'authoriz|visa|citizen',part,re.I):
                    denied_auth.extend(found)
                else:
                    denied_places.extend(found)
                continue
            value=canonical_exclusion(part)
            if value and len(value)<=80 and len(value.split())<=5 and value not in exclusions:
                exclusions.append(value)
    if re.search(r'\bnon[- ]clinical\b',positive,re.I):
        exclusions.extend(v for v in ['therapy','crisis counseling','direct clinical treatment'] if v not in exclusions)
    allowed=[state.title() for state in sorted(state_names(positive))]
    for name in location_requests(positive):
        display={'us':'USA','usa':'USA','united states':'USA','uk':'UK','united kingdom':'UK'}.get(name,name.title())
        if display not in allowed:
            allowed.append(display)
    for word in WORLD:
        if re.search(r'\b'+word+r'\b',positive,re.I) and not (word=='global' and re.search(r'\bglobal\s+(?:health|healthcare|warming|development|program|policy|organizations?)\b',positive,re.I)):
            allowed.append('Worldwide');break
    # Preserve the user's specified compensation units. A bare $ follows the
    # existing USD convention, but no cross-currency conversion is implied.
    salary=None
    unresolved=[]
    # Timezone overlap and scheduling aren't represented in catalogue fields.
    # Preserve their uncertainty rather than claiming verified eligibility.
    if re.search(r'\b(?:UTC|GMT)\s*[+-]\s*\d|\b(?:EST|EDT|PST|PDT|CET|CEST)\b|\b(?:time ?zone|working hours|hours overlap)\b', positive, re.I):
        unresolved.append('working_timezone')
    # A two-letter postal code can also name a country (CA, IN, etc.).
    # Require clarification rather than silently dropping ambiguous geography.
    codes=re.findall(r'\b(?:in|within|from|based in)\s+([A-Z]{2})\b',positive)
    if any(code not in {'US','UK','GB','EU'} for code in codes):
        unresolved.append('location_code')
    amount=r'(?P<currency>US\$|C\$|A\$|USD|EUR|GBP|CAD|AUD|CHF|NOK|INR|\$|£|€)?\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<k>k\b|thousand\b)?'
    pattern=r'(?:at least|minimum(?: salary)?|min(?:imum)? pay|over|above|from|starting at|salary(?: of)?|pay(?: of)?)\s*'+amount
    match=re.search(pattern,positive,re.I)
    if not match:
        match=re.search(amount+r'\s*(?:or more|and up|minimum|\+)',positive,re.I)
    if not match:
        # A stated rate with its unit ("$40/hour", "80 thousand a year") is a pay requirement on its own.
        match=re.search(amount+r'\s*(?=(?:a|per|/)\s*(?:year|yr|annum|hour|hr|month)\b)',positive,re.I)
        if match and not (match.group('currency') or match.group('k')):match=None
    if match and not match.group('currency') and not re.search(r'\b(?:salary|pay|wages?|compensation|remuneration|a year|per year|annually)\b',positive[max(0,match.start()-40):match.end()+40],re.I):
        match=None
    if match:
        value=Decimal(match.group('amount').replace(',',''))*(1000 if match.group('k') else 1)
        cur=match.group('currency')
        currency={'$':'USD','US$':'USD','£':'GBP','€':'EUR','C$':'CAD','A$':'AUD'}.get(cur,cur.upper() if cur else None)
        tail=positive[match.end():match.end()+50]
        period='year'
        for unit, pattern in [('hour',r'(?:per|/)\s*(?:hour|hr)|hourly'),('month',r'(?:per|/)\s*month|monthly'),('week',r'(?:per|/)\s*week|weekly'),('day',r'(?:per|/)\s*day|daily')]:
            if re.search(pattern,tail,re.I):period=unit;break
        salary={'amount':str(value),'currency':currency,'period':period}
        if re.search(r'\b(?:minimum salary|guaranteed|actual annual|not full.time.equivalent)\b',positive,re.I):salary['mode']='floor'
        if currency is None:
            unresolved.append('salary_currency')
    domains=[]
    for phrase in ['behavioral health','behavioural health','mental health','public health']:
        if re.search(r'\b'+phrase+r'\b',positive,re.I):domains.append(phrase)
    return {'constraint_version':4,'intent_text':text,'candidate_years':candidate_years,
            'required_employer_type':'nonprofit' if re.search(r'\bnon[- ]?profits?\b',positive,re.I) else None,
            'actual_annual_pay':bool(re.search(r'\bactual\s+annual\s+pay\b',positive,re.I)),
            'part_time_contract':bool(re.search(r'\bpart[- ]time\s+contract\b',positive,re.I)),'exclude_keywords':exclusions[:20],
            'locations':allowed[:12],'excluded_locations':list(dict.fromkeys(denied_places)),
            'excluded_work_authorization':list(dict.fromkeys(denied_auth)),
            'salary_requirement':salary,'min_salary':int(Decimal(salary['amount'])) if salary and salary['period']=='year' else None,
            'required_domains':domains,'unresolved_constraints':unresolved,'require_verified_location':bool(allowed)}


# Extract matching text only; this is never an HTML sanitizer or renderer.
# Quoted attributes may contain > and ordinary text comparisons must survive.
HTML_TAGS=re.compile(r"""<(?:!--.*?--|/?[A-Za-z](?:[^<>"']|"[^"]*"|'[^']*')*)>""",re.S)


def plain_job_field(job, field):
    cached=getattr(job,'_scout_plain_fields',None)
    if cached is None:cached={};job._scout_plain_fields=cached
    if field not in cached:
        raw=str(getattr(job,field,'') or '')
        other='description' if field=='requirements' else 'requirements' if field=='description' else None
        if other in cached and raw==str(getattr(job,other,'') or ''):
            cached[field]=cached[other]
        else:
            clean=' '.join(HTML_TAGS.sub(' ',raw).split())
            cached[field]=re.sub(r'\s+([.,;:!?])',r'\1',clean)
    return cached[field]


def location_compatibility(job, desired):
    if not desired:
        return None
    location=plain_job_field(job,'location')
    country=plain_job_field(job,'country')
    structured_location=source_location(job)
    if structured_location:
        location=structured_location
    body=' '.join(plain_job_field(job,f) for f in ['description','requirements'])
    body=re.sub(r'\bU\.S\.(?:A\.)?', 'USA', body, flags=re.I)
    title=plain_job_field(job,'title')
    # Parenthetical workplace qualifiers are source eligibility, even when an
    # importer has collapsed its location field to Remote.
    title_regions=re.findall(r'\b(?:hub[- ]remote|remote)\s*[:(]\s*([^)]*)\)',title,re.I)
    if title_regions:
        location='; '.join(title_regions)
        if re.search(r'\b(?:DC|Philly)\b',location):location += ' United States'
    location=re.sub(r'\bU\.S\.(?:A\.)?', 'USA', location, flags=re.I)
    evidence=location
    wanted=expand(desired)
    if state_names(' '.join(str(v) for v in desired)):wanted.add('US')
    # Explicit source exclusions override broad Remote/worldwide labels.
    for clause in re.split(r'[.!?;\n]',body+';'+location):
        if re.search(r'\b(?:not eligible|not available|cannot hire|unable to hire|excluding|except)\b',clause,re.I):
            denied=expand([clause])
            if state_names(clause):denied.add('US')
            if denied & wanted:return False
    # Positive restrictions in substantive source text beat lossy country labels.
    restrict=[]
    for clause in re.split(r'[.!?;\n]',body):
        lowered=clause.lower()
        if not any(token in lowered for token in ('remote','must','need to','only','candidates','applicants')):continue
        if re.search(r'remote\s+(?:within|from|in)|(?:must|need to|only)\s+(?:be\s+)?(?:based|reside|located)|(?:candidates|applicants)\s+(?:must\s+)?(?:be\s+)?(?:based|reside|located)',clause,re.I):
            if places(clause) or state_names(clause):
                # Negation applies to residency, not unrelated job duties.
                if not re.search(r"\b(?:not|n't)\s+(?:be\s+)?(?:based|reside|located)|\bnot\s+remote\s+(?:in|within|from)",clause,re.I):restrict.append(clause)
    offered=expand(restrict or [location,country])
    offered_states=state_names(' '.join(restrict or [location]))
    desired_states=state_names(' '.join(str(v) for v in desired))
    if offered_states:offered.add('US')
    explicit_global=bool(re.search(r'\b(?:worldwide|anywhere|globally|global)\b',location,re.I))
    if restrict:
        explicit_global=False
    requested_global=bool({str(v).lower() for v in desired}&WORLD)
    if requested_global:
        if offered:return False
        return True if explicit_global else None
    wanted=expand(desired)
    if desired_states:wanted.add('US')
    if offered_states and desired_states and not offered_states & desired_states:
        return False
    if offered:
        return bool(offered&wanted) if wanted else None
    if explicit_global:
        return True
    # Geography in titles is only considered when it explicitly qualifies the
    # eligible region, not when US appears as the subject of the work.
    title=plain_job_field(job,'title')
    if re.search(r'any (?:african|european|asian) country',title,re.I):
        region='africa' if 'african' in title.lower() else ('europe' if 'european' in title.lower() else 'asia')
        return bool(REGIONS[region]&wanted)
    return None


@dataclass(frozen=True)
class Eligibility:
    allowed: bool
    reason: str = ''
    location: bool | None = None
    caveats: tuple = ()


# Shown to the seeker when a job is offered without full verification.
CAVEAT_TEXT = {
    'review_remote': 'Check: fully remote arrangement not confirmed',
    'review_location': 'Check: hiring location not confirmed by listing',
    'review_seniority': 'Check: requested seniority not confirmed',
    'review_skills': 'Check: required skills may not be core to this role',
    'review_salary': 'Check: requested pay not confirmed',
    'listing_age': 'Check: older posting; confirm the employer is still hiring',
    'salary_unlisted': 'Check: salary not listed',
    'salary_other_currency': 'Check: salary is in another currency',
    'location_unconfirmed': 'Check: location eligibility not confirmed',
    'working_timezone': 'Check: working hours not stated',
    'location_code': 'Check: location eligibility not confirmed',
    'salary_currency': 'Check: salary currency',
    'paid_currency_unconfirmed': 'Check: payment in the requested currency is not confirmed',
}


def salary_caveat(job, requirement):
    """A soft salary miss: the listing has no comparable pay, not a lower one."""
    if not requirement:
        return None
    if getattr(job,'salary_min',None) is None and getattr(job,'salary_max',None) is None:
        return 'salary_unlisted'
    currency=requirement.get('currency')
    if currency and str(getattr(job,'salary_currency','') or '').upper() not in ('',currency.upper()):
        return 'salary_other_currency'
    return None


def salary_compatible(job, requirement):
    if not requirement:
        return True
    try:
        amount=Decimal(str(requirement['amount']))
        if not amount.is_finite() or amount<0:return False
    except (KeyError,TypeError,InvalidOperation):return False
    currency=requirement.get('currency')
    if not currency or str(getattr(job,'salary_currency','')).upper()!=currency.upper():return False
    raw={} if hasattr(job,'scout_salary_period') else (getattr(job,'raw_data',{}) or {})
    # The model salary fields are annual normalized amounts. Any source-period
    # evidence indicating raw unannualized amounts requires normalization first.
    period=str((job.scout_salary_period or 'year') if hasattr(job,'scout_salary_period') else raw.get('salary_period','year')).lower()
    if period not in {'year','annual','annually','annum','yearly'}:return False
    source_period=str(source_value(job,'scout_source_period','salaryPeriod') or 'year').lower()
    if source_period not in {'year','annual','annually','annum','yearly','month','monthly'}:return False
    maximum=getattr(job,'salary_min',None) if requirement.get('mode')=='floor' else getattr(job,'salary_max',None)
    if maximum is None:maximum=getattr(job,'salary_min',None)
    if maximum is None:return False
    # Hourly requirements cannot safely assume hours/year. Monthly is exactly
    # convertible for an annualized salary; hourly requires matching evidence.
    if requirement.get('period','year') not in {'year','month'}:return False
    if requirement.get('period')=='month':amount*=12
    try:
        maximum=Decimal(str(maximum))
        return maximum.is_finite() and maximum>=amount
    except (InvalidOperation,ValueError,TypeError):return False


def evaluate_job(job, prefs, relaxed=False):
    """Hard eligibility for a Scout.

    Strict mode (default) fails closed on anything unverified. ``relaxed`` keeps
    known conflicts as rejections but lets unverified salary, location or
    scheduling through with explicit caveats, so a Scout can show "possible"
    matches instead of nothing.
    """
    from jobs.services.importers.common import _is_generic_listing_title
    title = plain_job_field(job, 'title')
    if _is_generic_listing_title(title) or re.search(r'\b(?:spontaneous|unsolicited)\s+applications?\b', title, re.I):
        return Eligibility(False, 'not_job_opening')
    caveats=[]
    levels=prefs.get('experience_levels',[])
    level=getattr(job,'experience_level','')
    if levels and level and level not in levels:return Eligibility(False,'experience')
    types=prefs.get('job_types',[])
    if types and getattr(job,'job_type','') not in types:return Eligibility(False,'job_type')
    domains=prefs.get('required_domains',[])
    if domains and not any(domain_evidence(job,domain) for domain in domains):
        return Eligibility(False,'professional_domain')
    fields=' '.join(plain_job_field(job,f) for f in ['title','description','requirements'])
    fields_lower=fields.lower()
    from jobs.services.scout_review import workplace_conflict,dominant_function_conflict
    if dominant_function_conflict(fields,prefs,title):return Eligibility(False,"primary_function")
    if workplace_conflict(job, prefs, fields):return Eligibility(False, 'workplace')
    if experience_conflict(job,prefs,fields):return Eligibility(False,'experience_evidence')
    if role_conflict(job,prefs,fields):return Eligibility(False,'role_evidence')
    if prefs.get('required_employer_type')=='nonprofit':
        if not hasattr(job,'_scout_nonprofit_status'):job._scout_nonprofit_status=nonprofit_compatibility(job,fields)
        if job._scout_nonprofit_status is False:return Eligibility(False,'employer_type')
    if actual_pay_conflict(job,prefs,fields):return Eligibility(False,'actual_annual_pay')
    for value in prefs.get('exclude_keywords',[]):
        value=canonical_exclusion(str(value))
        if re.search(r'on[- ]?call',value,re.I):
            for clause in re.split(r'[.!?;\n]|\bbut\b',fields,flags=re.I):
                if re.search(r'\bon[- ]?call\b',clause,re.I) and not re.search(r'\b(?:no|without)\s+on[- ]?call|on[- ]?call\s+(?:is\s+)?not\s+required',clause,re.I):return Eligibility(False,'on_call')
            continue
        if value and value in fields_lower and re.search(r'(?<!\w)'+re.escape(value)+r'(?:s)?(?!\w)',fields,re.I):
            return Eligibility(False,'excluded_keyword')
    requirement=prefs.get('salary_requirement')
    if not requirement and prefs.get('min_salary'):
        requirement={'amount':str(prefs['min_salary']),'currency':'USD','period':'year'}
    paid_requested=bool(requirement or re.search(r'\bpaid\b|\b(?:salary|compensation|pay)\b',prefs.get('intent_text',''),re.I))
    volunteer_title=bool(re.search(r'\(volunteer\)|^volunteer\s*[,:(-]|^volunteer$',title,re.I))
    if paid_requested and (volunteer_title or re.search(r'\bunpaid\b|\bvolunteer (?:role|position|opportunity)\b',fields,re.I)):
        return Eligibility(False,'salary')
    currency_request=re.search(r'\bpaid in (US dollars|USD|EUR|euros|GBP|British pounds)\b',prefs.get('intent_text',''),re.I)
    if currency_request:
        requested_currency={'us dollars':'USD','usd':'USD','eur':'EUR','euros':'EUR','gbp':'GBP','british pounds':'GBP'}[currency_request.group(1).lower()]
        has_pay=getattr(job,'salary_min',None) is not None or getattr(job,'salary_max',None) is not None
        actual_currency=str(getattr(job,'salary_currency','') or '').upper()
        if has_pay and actual_currency and actual_currency!=requested_currency:return Eligibility(False,'salary')
        if not has_pay or not actual_currency:
            if not relaxed:return Eligibility(False,'salary')
            caveats.append('paid_currency_unconfirmed')
    if not salary_compatible(job,requirement):
        allow_unconfirmed=relaxed and not re.search(r'\bguaranteed\b',prefs.get('intent_text',''),re.I)
        caveat=salary_caveat(job,requirement) if allow_unconfirmed else None
        if not caveat:return Eligibility(False,'salary')
        caveats.append(caveat)
    if prefs.get('unresolved_constraints'):
        if not relaxed:return Eligibility(False,'unresolved_constraint')
        caveats.extend(prefs['unresolved_constraints'])
    location=location_compatibility(job,prefs.get('locations',[]))
    if location is False:
        return Eligibility(False,'location',location)
    if location is None and prefs.get('locations') and prefs.get('require_verified_location',True):
        if not relaxed:return Eligibility(False,'location',location)
        caveats.append('location_unconfirmed')
    if expand(prefs.get('excluded_locations',[])) & expand([getattr(job,'location',''),getattr(job,'country','')]):
        return Eligibility(False,'excluded_location',location)
    for value in prefs.get('excluded_work_authorization',[]):
        for name in places(value):
            variants=[name] + [alias for alias,code in ALIASES.items() if code in expand([value])]
            authorization_clauses=[clause for clause in re.split(r'[.!?;\n]|\bbut\b|\band\b',fields,flags=re.I)
                if not re.search(r"\bnot\s+(?:required|necessary)|\b(?:do not|does not|don't|doesn't)\s+require|\bno\s+(?:US|USA|United States)\s+(?:work )?(?:authorization|citizenship)",clause,re.I)]
            if any(re.search(r'(?:'+re.escape(alias)+r')\s+(?:work )?(?:authorization|citizenship).{0,50}(?:required|must)|(?:require|must have).{0,50}\b'+re.escape(alias)+r'\b',clause,re.I) for alias in variants for clause in authorization_clauses):
                return Eligibility(False,'work_authorization',location)
    for skill in prefs.get('required_skills',[]):
        if not re.search(r'(?<!\w)'+re.escape(skill)+r'(?!\w)',fields,re.I):return Eligibility(False,'required_skill',location)
    return Eligibility(True,location=location,caveats=tuple(dict.fromkeys(caveats)))
