"""Versioned, source-grounded extraction contract. Unknown values stay unknown."""
import hashlib
import html
import re
from decimal import Decimal, InvalidOperation
from jobs.constants import IMPACT_AREA_SLUGS
from jobs.constants.skills import SKILLS_BY_SLUG, normalize_skill_slugs

VERSION = 'job-facts-v3'

def plain(value):
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', str(value or ''))).split())

def source_hash(title, organization, description):
    from jobs.services.extraction_source import compact_source
    return hashlib.sha256('\n'.join(map(plain, (title, organization, compact_source(description)))).encode()).hexdigest()

def supported(quote, source):
    if isinstance(quote, list):
        return bool(quote) and all(isinstance(item, str) and supported(item, source) for item in quote)
    return isinstance(quote, str) and len(quote.strip()) >= 4 and plain(quote).casefold() in plain(source).casefold()

def validate(data, title, organization, description):
    """Reject invalid shapes/labels and untraceable evidence; retain rejected field names.

    Quote existence is a grounding check, not proof of semantic correctness. The
    model's interpretation must also pass the offline evaluation before rollout.
    """
    if not isinstance(data, dict):
        return {}
    source = '\n'.join((title, organization, description))
    facts = {'version': VERSION, 'source_hash': source_hash(title, organization, description), 'rejected': []}
    choices = {
        'impact_area': set(IMPACT_AREA_SLUGS),
        'job_type': {'full-time', 'part-time', 'contract', 'freelance'},
        'experience_level': {'internship', 'entry', 'mid', 'senior', 'executive'},
        'education_level': {'high_school', 'associate', 'bachelor', 'master', 'phd'},
    }
    for field, allowed in choices.items():
        item = data.get(field)
        facts[field] = None
        if item is not None:
            if isinstance(item, dict) and isinstance(item.get('value'), str) and item['value'] in allowed and isinstance(item.get('evidence'), str) and supported(item.get('evidence'), source):
                if field == 'education_level':
                    quote = plain(item['evidence']).casefold()
                    if re.search(r'preferred|not required|or equivalent|equivalent experience', quote):
                        facts['rejected'].append(field)
                        continue
                    if item['value'] == 'phd' and not re.search(r'\bph\.?d\b|doctorate|doctoral', quote):
                        facts['rejected'].append(field)
                        continue
                value = item['value']
                if field == 'experience_level' and plain(item['evidence']) == plain(title):
                    # Explicit leadership titles have a defined taxonomy value;
                    # this is normalization, not inference from generic prose.
                    if re.match(r'^(?:(?:senior|associate|assistant|managing|deputy)\s+)?director\b|^vice president\b|^vp\b|^head of\b|^(?:ceo|cto|cfo|coo)\b', plain(title), re.I):
                        value = 'executive'
                facts[field] = {'value': value, 'evidence': item['evidence']}
            else:
                facts['rejected'].append(field)
    for field in ('required_skills', 'preferred_skills'):
        facts[field] = []
        items = data.get(field, [])
        if not isinstance(items, list):
            facts['rejected'].append(field)
            continue
        seen = set()
        for item in items:
            labels = normalize_skill_slugs([item.get('value')]) if isinstance(item, dict) else []
            if labels and isinstance(item.get('evidence'), str) and supported(item.get('evidence'), source):
                label = labels[0]
                if label not in seen:
                    facts[field].append({'value': label, 'evidence': item['evidence']})
                    seen.add(label)
            else:
                facts['rejected'].append(field)
    facts['geography'] = None
    geo = data.get('geography')
    if geo is not None:
        from django_countries import countries
        codes = set(dict(countries)) | {'XK'}
        lists = ('countries', 'excluded_countries', 'regions', 'restrictions')
        valid = isinstance(geo, dict) and geo.get('scope') in {'global', 'restricted', 'unknown'}
        valid = valid and all(isinstance(geo.get(key), list) and all(isinstance(x, str) and len(x) <= 300 for x in geo[key]) for key in lists)
        valid = valid and supported(geo.get('evidence'), source)
        if valid:
            valid = all(x in codes for x in geo['countries'] + geo['excluded_countries'])
            valid = valid and not (set(geo['countries']) & set(geo['excluded_countries']))
            valid = valid and not (geo['scope'] == 'global' and any(geo[k] for k in lists))
        if valid:
            facts['geography'] = {k: geo[k] for k in ('scope', *lists, 'evidence')}
        else:
            facts['rejected'].append('geography')
    from jobs.services.extraction_geography import conflicting_residency
    conflicts = conflicting_residency(description)
    if conflicts:
        facts['geography'] = {
            'scope': 'unknown', 'countries': [], 'excluded_countries': [],
            'regions': [], 'restrictions': conflicts, 'evidence': conflicts,
        }
        facts['blocked_fields'] = ['geography']
        facts['geography_conflicts'] = conflicts
        facts['rejected'].append('geography')
    facts['salary'] = None
    salary = data.get('salary')
    if salary is not None:
        valid = isinstance(salary, dict) and isinstance(salary.get('evidence'), str) and supported(salary.get('evidence'), source)
        valid = valid and salary.get('period') in {'year', 'month', 'hour', 'week', 'day', 'unknown'}
        valid = valid and bool(re.fullmatch(r'[A-Z]{3}', str(salary.get('currency', ''))))
        amounts = []
        try:
            for key in ('min', 'max'):
                value = salary.get(key) if isinstance(salary, dict) else None
                amount = Decimal(str(value)) if value is not None and not isinstance(value, bool) else None
                if value is not None and (amount is None or not amount.is_finite() or not 0 < amount <= 100000000):
                    valid = False
                amounts.append(float(amount) if amount is not None and amount.is_finite() else None)
        except (InvalidOperation, ValueError, TypeError):
            valid = False
        if len(amounts) != 2 or not any(amounts) or (all(x is not None for x in amounts) and amounts[0] > amounts[1]):
            valid = False
        if valid:
            quote = plain(salary['evidence'])
            quoted_amounts = set()
            for match in re.finditer(r'(?<![\w.])([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*([kK])?(?!\w)', quote):
                quoted_amounts.add(float(match.group(1).replace(',', '')) * (1000 if match.group(2) else 1))
            valid = all(amount is None or amount in quoted_amounts for amount in amounts)
            units = {'year': r'year|annual|annum|/yr', 'month': r'month|/mo', 'hour': r'hour|/hr', 'week': r'week|/wk', 'day': r'daily|per day|/day'}
            if salary['period'] in units:
                valid = valid and bool(re.search(units[salary['period']], quote, re.I))
            symbols = {'EUR': '€', 'GBP': '£', 'USD': 'US$', 'CAD': 'C$', 'AUD': 'A$'}
            valid = valid and (bool(re.search(r'\b' + salary['currency'] + r'\b', quote)) or symbols.get(salary['currency'], '\x00') in quote)
        if valid:
            facts['salary'] = {key: salary[key] for key in ('currency', 'period', 'evidence')}
            facts['salary'].update(min=amounts[0], max=amounts[1])
        else:
            facts['rejected'].append('salary')
    return facts

def legacy_fields(facts):
    """Adapter for existing import, search and source-page enrichment consumers."""
    result = {'job_facts': facts}
    for field in ('impact_area', 'job_type', 'experience_level', 'education_level'):
        item = facts.get(field)
        if item:
            result[field] = item['value']
            result[field.replace('_level', '') + '_evidence'] = item['evidence']
    result['skills'] = list(dict.fromkeys(item['value'] for key in ('required_skills', 'preferred_skills') for item in facts.get(key, [])))
    geo = facts.get('geography')
    # A single-country column cannot represent a multi-country or regional rule.
    if geo and geo['scope'] == 'restricted' and len(geo['countries']) == 1 and not any(geo[k] for k in ('regions', 'restrictions', 'excluded_countries')):
        from django_countries import countries
        result['country'] = 'Kosovo' if geo['countries'][0] == 'XK' else str(countries.name(geo['countries'][0]))
    salary = facts.get('salary')
    if salary and salary['period'] in {'year', 'month'}:
        multiplier = 12 if salary['period'] == 'month' else 1
        for key in ('min', 'max'):
            result['salary_' + key] = salary[key] * multiplier if salary[key] is not None else None
        result.update(salary_currency=salary['currency'], salary_evidence=salary['evidence'])
    return result


def source_description(payload):
    """Use the same complete input in batch imports and direct parser batches."""
    return '\n'.join(dict.fromkeys(str(payload.get(key) or '') for key in ('description', 'requirements', 'location')))


def reusable_facts(facts, title, organization, description, model, contract_hash):
    """A cache hit still needs a matching contract and source-grounded values."""
    from jobs.services.extraction_contract import FIELDS
    if not isinstance(facts, dict) or not set(FIELDS) <= facts.keys():
        return False
    if (facts.get('version') != VERSION or facts.get('source_hash') != source_hash(title, organization, description)
            or facts.get('model') != model or facts.get('contract_hash') != contract_hash
            or facts.get('unresolved_fields') or facts.get('blocked_fields')):
        return False
    return not validate(facts, title, organization, description).get('rejected')


def annual_salary_matches(facts, title, organization, description, minimum, maximum, currency):
    """Only source-validated, numerically matching facts can establish annual pay."""
    if not isinstance(facts, dict) or facts.get('version') != VERSION or not facts.get('salary'):
        return False
    checked = validate({'salary': facts['salary']}, title, organization, description)
    if not checked.get('salary') or checked['salary']['period'] not in {'year', 'month'}:
        return False
    annual = legacy_fields(checked)
    if currency != annual.get('salary_currency'):
        return False
    for key, stored in (('min', minimum), ('max', maximum)):
        expected = annual.get('salary_' + key)
        if expected is None or stored is None:
            if expected is not None or stored is not None:
                return False
        elif Decimal(str(expected)) != Decimal(str(stored)):
            return False
    return True
