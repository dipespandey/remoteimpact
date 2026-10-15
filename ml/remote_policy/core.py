"""Conservative remote-location rules with attributable evidence.

Decisions concern stated remote-work conditions, not legal eligibility or hiring.
Unknown is a first-class outcome; a generic 'Remote' label is never worldwide.
"""
from dataclasses import asdict, dataclass, field
from html import unescape
import re
import json
from pathlib import Path

# English ISO names from django-countries (MIT); no Django settings dependency.
COUNTRY_NAMES = json.loads(Path(__file__).with_name('countries.json').read_text())
countries = tuple(COUNTRY_NAMES.items())

LABELS = ('compatible', 'incompatible', 'unknown')
EU = frozenset('AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE'.split())
EUROPE = EU | frozenset('AL AD BA BY CH GB IS LI MC MD ME MK NO RS RU SM TR UA VA XK'.split())
REGIONS = {
    'european union': EU, 'eu': EU, 'eea': EU | {'IS', 'LI', 'NO'},
    'europe': EUROPE, 'north america': {'CA', 'US', 'MX'},
    'latin america': set('AR BO BR CL CO CR CU DO EC SV GT HT HN MX NI PA PY PE PR UY VE'.split()),
    'latam': set('AR BO BR CL CO CR CU DO EC SV GT HT HN MX NI PA PY PE PR UY VE'.split()),
    'apac': set('AU NZ NP IN BD PK LK CN JP KR SG MY ID PH TH VN TW HK'.split()),
    'emea': EUROPE | set('DZ AO BJ BW BF BI CM CD CG CI EG ET GH KE MA MG MW ML MZ NA NE NG RW SN SO ZA SS SD TZ TG TN UG ZM ZW AE SA IL QA BH OM KW JO LB'.split()),
}
NAMES = {str(name).lower(): code for code, name in countries}
NAMES.update({'united states': 'US', 'usa': 'US', 'u.s.': 'US', 'us': 'US',
              'united kingdom': 'GB', 'uk': 'GB', 'uae': 'AE', 'south korea': 'KR',
              'czech republic': 'CZ', 'vietnam': 'VN', 'russia': 'RU'})
PLACE = re.compile(r'(?<![a-z])(?:' + '|'.join(re.escape(x) for x in sorted(set(NAMES) | set(REGIONS), key=len, reverse=True)) + r')(?![a-z])', re.I)
STATE_NAMES = {'california': 'CA', 'new york': 'NY', 'texas': 'TX', 'florida': 'FL',
               'colorado': 'CO', 'washington': 'WA', 'massachusetts': 'MA',
               'alaska': 'AK', 'hawaii': 'HI'}


def clean(text):
    text = re.sub(r'<(?:br\b[^>]*|/p|/div|/li|/h[1-6])>', '\n', str(text or ''), flags=re.I)
    return re.sub(r'[ \t]+', ' ', unescape(re.sub(r'<[^>]*>', ' ', text))).strip()


@dataclass(frozen=True)
class Candidate:
    country: str
    subdivision: str | None = None
    utc_offset: float | None = None
    authorized_countries: tuple[str, ...] | None = None
    employment_type: str | None = None

    def __post_init__(self):
        country = self.country.upper()
        if country not in dict(countries):
            raise ValueError('country must be a supported ISO 3166-1 alpha-2 code')
        object.__setattr__(self, 'country', country)
        if self.utc_offset is not None and not -12 <= self.utc_offset <= 14:
            raise ValueError('utc_offset must lie between -12 and +14')
        if self.authorized_countries is not None:
            codes = tuple(x.upper() for x in self.authorized_countries)
            if any(x not in dict(countries) for x in codes):
                raise ValueError('authorized_countries must contain ISO country codes')
            object.__setattr__(self, 'authorized_countries', codes)

    def text(self):
        name = dict(countries)[self.country]
        parts = [f'Applicant lives in {name} ({self.country}).']
        if self.subdivision: parts.append(f'State/province: {self.subdivision}.')
        if self.utc_offset is not None: parts.append(f'Residential UTC offset: {self.utc_offset:+g}.')
        if self.authorized_countries is not None:
            parts.append('Known work authorization: ' + (', '.join(str(dict(countries)[x]) for x in self.authorized_countries) or 'none') + '.')
        if self.employment_type: parts.append(f'Contract type: {self.employment_type}.')
        return ' '.join(parts)


@dataclass
class Decision:
    label: str = 'unknown'
    evidence: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    backend: str = 'rules'
    confidence: float | None = None

    def to_dict(self): return asdict(self)


def place_codes(text):
    codes = set()
    for match in PLACE.finditer(text):
        token = match[0].lower()
        # Lowercase 'us' is ordinarily a pronoun, not a country.
        if token == 'us' and match[0] != 'US': continue
        codes.update(REGIONS[token] if token in REGIONS else {NAMES[token]})
    return codes


def sentences(text):
    # Keep decimal offsets and dotted US abbreviations intact.
    for match in re.finditer(r'[^\n!?;]+(?:[!?;]|$)', text):
        for part in re.split(r'(?<=[a-z0-9])\.(?=\s+[A-Z])', match[0]):
            part = part.strip(' \n\t.;')
            if part: yield part


def worldwide_permission(clause):
    if re.search(r'\b(?:not|cannot|can\x27t|never)\s+(?:work|hire|hiring)\b', clause, re.I):
        return False
    return bool(re.search(r'\b(?:work from anywhere|remote worldwide|worldwide remote|anywhere in the world|work from any country|globally remote|worldwide applicants|hire worldwide|hiring worldwide|no matter (?:which|what) country you call home)\b', clause, re.I)) or bool(re.fullmatch(r'(?:location\s*:\s*)?(?:anywhere|worldwide|global)', clause.strip(' .'), re.I))


def rules(text, candidate, *, location=''):
    text = clean(text)
    if not text and not location:
        return Decision(reasons=['No source text.'])
    if len(text) > 100_000:
        return Decision(reasons=['Input exceeds the supported 100,000-character limit.'])
    result = Decision()
    checks = []
    permission = False
    source = ([('location', clean(location))] if location else []) + [('text', s) for s in sentences(text)]
    for field_name, clause in source:
        low = clause.lower()
        codes = place_codes(clause)
        permission = permission or worldwide_permission(clause)
        def record(status, reason):
            checks.append(status)
            result.evidence.append({'field': field_name, 'quote': clause, 'condition': reason})
        if re.search(r'\b(?:privacy notice|privacy rights|information we collect|consumer privacy act)\b', low):
            continue
        if re.search(r'\b(?:non.remote|not (?:a |an |fully )?remote)\b', low):
            record(None, 'The source does not establish a currently remote position.'); continue
        mentions = list(PLACE.finditer(clause))
        polarity = re.search(r'\b(?:but not|not (?:from|the)|excluding|except)\b', low)
        if len(mentions) > 1 and polarity and mentions[0].start() < polarity.start() < mentions[-1].start():
            positive, negative = place_codes(clause[:polarity.start()]), place_codes(clause[polarity.end():])
            if positive and negative and re.search(r'\b(?:remote|applicants?|candidates?|residents?|must|hiring|eligible)\b', low) and not re.search(r'\b(?:headquarters|customers?|clients?|offices?|authori[sz]\w*|work permit|right to work)\b', low):
                record(candidate.country in positive and candidate.country not in negative, 'Separately scoped country inclusion and exclusion.')
                permission = True
            else:
                record(None, 'Mixed geographic inclusion and exclusion need scoped parsing.')
            continue
        if re.search(r'\b(?:not required|no requirement|do not need|need not)\b.*\b(?:reside|live|based|located)\b', low):
            continue
        if re.search(r'\b(?:do not|don\x27t|not)\s+(?:exclude|excluding|restricted to|limited to)\b', low):
            continue
        if re.search(r'\b(?:citizenship|citizen|passport|security clearance|unless)\b', low):
            record(None, 'Citizenship, clearance or conditional exception needs additional details.'); continue
        if re.search(r'\bgeorgia\b', low) and not re.search(r'\b(?:country of georgia|state of georgia|united states|usa)\b', low):
            record(None, 'Georgia may refer to a country or a US state.'); continue
        # Branch-specific and vague conditions need semantic interpretation.
        if re.search(r'\b(?:contractors?|employees?)\b.*\b(?:but|whereas|while)\b|\b(?:except|excluding|unless)\b.*(?<!united )\b(?:states?|provinces?)\b', low):
            record(None, 'Conditional policy needs interpretation.'); continue
        if re.search(r'\b(?:authori[sz](?:ed|ation)|right to work|work permits?|visa sponsorship)\b', low):
            if codes and re.search(r'\b(?:must|requires?|required|need|already|existing|without sponsorship|no sponsorship)\b', low):
                status = None if candidate.authorized_countries is None else bool(codes & set(candidate.authorized_countries))
                record(status, 'Stated work authorization requirement.'); continue
            if re.search(r'\b(?:cannot|do not|does not|not able to|no)\b.*\bsponsor', low):
                record(None, 'Sponsorship unavailable; authorization requirements need clarification.'); continue
            record(None, 'Unresolved authorization or sponsorship condition.'); continue
        states = {code for name, code in STATE_NAMES.items() if re.search(r'\b' + name + r'\b', low)}
        if states and re.search(r'\b(?:reside|resident|residents|based|located|location|remote|eligible|hire|hiring|only)\b', low):
            excluded = bool(re.search(r'\b(?:not|exclude|excluding|except|cannot|unavailable)\b', low))
            if candidate.country != 'US': status = True if excluded else False
            elif not candidate.subdivision: status = None
            else:
                state = candidate.subdivision.upper().removeprefix('US-')
                state = STATE_NAMES.get(candidate.subdivision.lower(), state)
                status = (state not in states) if excluded else (state in states)
            record(status, 'Stated US state restriction.'); continue
        offsets = re.search(r'\b(?:utc|gmt)\s*([+-]\d{1,2}(?:\.\d+)?)\s*(?:to|through|and|–|-)\s*(?:(?:utc|gmt)\s*)?([+-]\d{1,2}(?:\.\d+)?)', low)
        if offsets:
            # Residential timezone is distinct from a person's willingness to work hours.
            if re.search(r'\b(?:overlap|working hours|work hours|available)\b', low):
                record(None, 'Working-hour overlap is not implied by residential timezone.'); continue
            lo, hi = map(float, offsets.groups())
            record(None if candidate.utc_offset is None or not -12 <= lo <= hi <= 14 else lo <= candidate.utc_offset <= hi, 'Stated residential UTC-offset range.'); continue
        if re.search(r'\b(?:overlap|business hours|working hours|time ?zones?|\b[cep]st\b|\b[cep]dt\b)\b', low) and re.search(r'\b(?:must|required|need|only|at least|within)\b', low):
            record(None, 'Timezone or working-hour condition cannot be resolved from supplied details.'); continue
        restriction = field_name == 'location' or bool(re.search(
            r'\b(?:must|need to|required to|have to)\s+(?:currently\s+)?(?:be|live|reside)|'
            r'\b(?:based|located|residing|resident|residents|live|living)\s+(?:only\s+)?(?:in|within|across)|'
            r'\b(?:remote|location)\s*[:(–-]|\bremote\s+(?:in|within|from|across)|'
            r'\b(?:hiring|recruiting|open to|available to|available in|restricted to|limited to|eligible locations|eligible countries|applicants from|candidates from)\b|'
            r'\b(?:only|exclusively)\s+(?:accept|hire|consider)|\bresidents of\b|\b(?:excluding|except)\b|\bnot from\b|'
            r'\b(?:home|residence)\s+(?:is|must be)\s+in\b|\bapplicants outside\b|\bonly\s*$', low))
        restriction = restriction or bool(re.search(r'\banywhere\s+(?:in|within)\b', low))
        # Customers, headquarters, travel and benefits are not applicant restrictions.
        irrelevant = bool(re.search(r'\b(?:headquartered|headquarters|customers?|clients?|benefits?|insurance|travel to|offices? in|markets? in)\b', low))
        explicit = bool(re.search(r'\b(?:must|only|required|restricted|limited|candidates|applicants|reside|residents)\b', low))
        policy_prefix = re.split(r',?\s+with\s+(?:an?\s+)?offices?\s+in\b', clause, maxsplit=1, flags=re.I)[0]
        if policy_prefix != clause and place_codes(policy_prefix) and re.search(r'\b(?:remote|based|location)\b', policy_prefix, re.I):
            codes = place_codes(policy_prefix)
            irrelevant = False
        if irrelevant and len(mentions) > 1 and restriction:
            record(None, 'Multiple geographic references have different possible scopes.'); continue
        direct_policy = bool(re.match(r'(?:(?:you|applicants|candidates) (?:must be |are |should be )?)?(?:based in|remote(?:-first)? (?:in|within))\b', low))
        if codes and restriction and (not irrelevant or explicit or direct_policy):
            excluded = bool(re.search(r'\b(?:exclude|excluding|except|unavailable|cannot (?:hire|live|reside)|not (?:from|in|within|available|eligible|based|located|residing|live|reside))\b', low))
            if 'applicants outside' in low and re.search(r'\bnot eligible\b', low): excluded = False
            record(candidate.country not in codes if excluded else candidate.country in codes, 'Stated country or regional restriction.')
            permission = permission or not excluded or worldwide_permission(clause)
            if re.search(r'\b(?:able to travel|must travel|required to travel)\b', low):
                record(None, 'Required travel availability was not supplied.')
        elif worldwide_permission(clause):
            record(True, 'Explicit worldwide remote statement.')
            permission = True
        elif re.search(r'\b(?:must|required|need to|mandatory)\b.*\b(?:office|on.?site|hybrid|commut|relocat)', low):
            record(None, 'Mandatory workplace attendance is outside country-only eligibility.')
        elif re.search(r'\b(?:must|required|only|restricted|limited)\b.*\b(?:remote|based|located|reside|residents|countries|country|location)\b', low):
            record(None, 'Unresolved location condition.')
    if False in checks:
        result.label = 'incompatible'
    elif checks and all(v is True for v in checks) and (permission or any(e['condition'] == 'Stated US state restriction.' for e in result.evidence)):
        result.label = 'compatible'
    else:
        result.reasons.append('Missing or unresolved conditions; remote alone does not establish worldwide access.')
    return result


def model_input(text, candidate, location=''):
    return candidate.text(), ('Location: ' + location + '\n' if location else '') + clean(text)
