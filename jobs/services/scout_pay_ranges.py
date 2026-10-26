"""Extract stated pay intervals without inferring currency or converting it."""
import re
from decimal import Decimal

CURRENCY = r'US\$|C\$|A\$|USD|EUR|GBP|CAD|AUD|CHF|NOK|INR|\$|£|€'
VALUE = r'\d[\d,]*(?:\.\d+)?'
RANGE = re.compile(
    rf'(?<![\w.])(?P<c1>{CURRENCY})?\s*(?P<a>{VALUE})\s*(?P<k1>k\b|thousand\b)?'
    rf'\s*(?:[-–—]|\bto\b|\band\b)\s*'
    rf'(?P<c2>{CURRENCY})?\s*(?P<b>{VALUE})\s*(?P<k2>k\b|thousand\b)?\s*(?P<plus>\+)?', re.I)


def pay_range(text):
    """Return (found, requirement, unresolved), leaving single amounts to the caller."""
    for match in RANGE.finditer(text):
        context = text[max(0, match.start()-65):match.start()]
        tail = text[match.end():match.end()+45]
        suffix = re.match(r'\s*(USD|EUR|GBP|CAD|AUD|CHF|NOK|INR)\b', tail, re.I)
        pay_context = re.search(r'\b(?:salary|pay|compensation|earn|earning|rate|wages?)\b', context, re.I)
        if re.match(r'\s*(?:years?\s+(?:of\s+)?experience|years?\s+old|hours?\s*(?:/|per\s+)week|%)\b', tail, re.I):
            continue
        if not pay_context and re.search(r'\b(?:budget|revenue|portfolio|raised|managed)\b', context, re.I):
            continue
        if not (match['c1'] or match['c2'] or suffix or pay_context):
            continue
        def currency(value):
            value = (value or '').upper()
            return {'$':'USD', 'US$':'USD', '£':'GBP', '€':'EUR', 'C$':'CAD', 'A$':'AUD'}.get(value, value or None)
        c1, c2 = currency(match['c1']), currency(match['c2'] or (suffix[1] if suffix else None))
        low, high = (Decimal(match[key].replace(',', '')) for key in ('a', 'b'))
        # $55–75k and 55k–75k share the explicitly stated thousands suffix.
        if match['k1'] or match['k2']:
            low *= 1000 if match['k1'] or low < 1000 else 1
            high *= 1000 if match['k2'] or high < 1000 else 1
        if low > high or c1 and c2 and c1 != c2:
            return True, None, ['salary_range']
        period = 'year'
        for unit, pattern in [('hour', r'(?:per|/|an?)\s*(?:hour|hr)|hourly'),
                              ('month', r'(?:per|/|a)\s*month|monthly'),
                              ('week', r'(?:per|/|a)\s*week|weekly'),
                              ('day', r'(?:per|/|a)\s*day|daily')]:
            if re.search(pattern, tail, re.I):
                period = unit
                break
        requirement = {'amount': str(low), 'max_amount': str(high),
                       'currency': c1 or c2, 'period': period}
        if match['plus']:
            requirement['upper_open'] = True
        return True, requirement, [] if c1 or c2 else ['salary_currency']
    return False, None, []
