"""Spend repair calls on recoverable errors, not known unsupported assertions."""
import re
from jobs.constants.skills import normalize_skill_slugs
from jobs.services.job_facts import plain, supported


def review_fields(facts, raw, source):
    selected = []
    for field in sorted(set(facts.get('rejected', []))):
        if field in facts.get('blocked_fields', []):
            continue
        item = raw.get(field)
        if field in {'required_skills', 'preferred_skills'} and isinstance(item, list):
            # Unlisted skills cannot be published under this taxonomy. A known
            # skill with a broken reference can be recovered by rereading source.
            recoverable = any(isinstance(value, dict)
                              and normalize_skill_slugs([value.get('value')])
                              and not supported(value.get('evidence'), source) for value in item)
            if not recoverable:
                continue
        if field == 'education_level' and isinstance(item, dict) and supported(item.get('evidence'), source):
            quote = plain(item['evidence']).casefold()
            if (re.search(r'preferred|not required|or equivalent|equivalent experience', quote)
                    or (item.get('value') == 'phd' and not re.search(r'\bph\.?d\b|doctorate|doctoral', quote))):
                continue
        if field == 'salary' and isinstance(item, dict) and supported(item.get('evidence'), source):
            quote = plain(item['evidence'])
            currency = str(item.get('currency', ''))
            symbols = {'EUR': '€', 'GBP': '£', 'USD': 'US$', 'CAD': 'C$', 'AUD': 'A$'}
            if (not re.fullmatch(r'[A-Z]{3}', currency)
                    or not (re.search(r'\b' + re.escape(currency) + r'\b', quote)
                            or symbols.get(currency, '\x00') in quote)):
                # The text does not establish this currency; guessing again is
                # not a recovery strategy. Retain a null salary.
                continue
        selected.append(field)
    return selected
