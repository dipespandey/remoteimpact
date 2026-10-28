"""Original contract totals only; never derive pay from annual equivalents."""
import re
from decimal import Decimal

_QUOTE = re.compile(
    r'(?P<low>\d[\d,]*(?:\.\d+)?)\s*[–—-]\s*\$?'
    r'(?P<high>\d[\d,]*(?:\.\d+)?)\s+USD\s+for\s+(?:the\s+)?'
    r'(?P<duration>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)'
    r'[- ](?P<unit>month|week|day)s?\s+(?:fellowship|contract|engagement|assignment)\b', re.I)
_WORDS = {word: i for i, word in enumerate(
    'one two three four five six seven eight nine ten eleven twelve'.split(), 1)}


def quoted_term_compensation(text):
    """Abstain on unsupported formats or multiple different quoted totals."""
    candidates = []
    for match in _QUOTE.finditer(text or ''):
        prefix = re.split(r'[.!?;\n]', (text or '')[:match.start()])[-1]
        if re.search(r'\b(?:budget|funding|grant|revenue|annualized|annualised)\b', prefix, re.I):
            continue
        low = Decimal(match['low'].replace(',', ''))
        high = Decimal(match['high'].replace(',', ''))
        duration = _WORDS.get(match['duration'].lower())
        if duration is None:
            duration = int(match['duration'])
        if not (0 < low <= high <= 10_000_000 and 0 < duration <= 365):
            continue
        value = {'minimum': str(low), 'maximum': str(high), 'currency': 'USD',
                 'duration_value': duration, 'duration_unit': match['unit'].lower(),
                 'basis': 'total_contract', 'evidence': match.group(0)}
        if value not in candidates:
            candidates.append(value)
    return candidates[0] if len(candidates) == 1 else None
