"""Evidence-based salary extraction for job descriptions."""

from __future__ import annotations

import re
from typing import Any


_CURRENCY_CODES = {
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "£": "GBP",
    "GBP": "GBP",
    "€": "EUR",
    "EUR": "EUR",
    "CAD": "CAD",
    "C$": "CAD",
    "AUD": "AUD",
    "A$": "AUD",
    "CHF": "CHF",
}
_CURRENCY = r"(?:US\$|C\$|A\$|\$|£|€|USD|GBP|EUR|CAD|AUD|CHF)"
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*[kK]?"
_PERIOD = r"(?:per\s+|/\s*)?(year|annual(?:ly)?|annum|yr|hour|hourly|hr|month|monthly)"
_LABEL = r"(?:salary|compensation|pay\s+range|base\s+pay|base\s+salary|annual\s+pay|hourly\s+rate)"


def extract_annual_salary(text: str) -> dict[str, Any]:
    """Return an annual salary only when compensation context and units are explicit."""
    if not text:
        return {}

    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))
    patterns = (
        rf"(?P<label>{_LABEL})[^.;\n]{{0,45}}?(?P<c1>{_CURRENCY})\s*(?P<n1>{_NUMBER})"
        rf"\s*(?:-|–|—|to)\s*(?P<c2>{_CURRENCY})?\s*(?P<n2>{_NUMBER})\s*(?P<period>{_PERIOD})?",
        rf"(?P<c1>{_CURRENCY})\s*(?P<n1>{_NUMBER})\s*(?:-|–|—|to)\s*"
        rf"(?P<c2>{_CURRENCY})?\s*(?P<n2>{_NUMBER})\s*(?P<period>{_PERIOD})"
    )

    for pattern in patterns:
        match = re.search(pattern, plain, re.IGNORECASE)
        if not match:
            continue
        minimum = _amount(match.group("n1"))
        maximum = _amount(match.group("n2"))
        period = (match.group("period") or "").lower()
        minimum, maximum = _annualize(minimum, maximum, period)
        if _valid_range(minimum, maximum):
            currency = _currency_code(match.group("c1"))
            return {
                "salary_min": minimum,
                "salary_max": maximum,
                "salary_currency": currency,
                "salary_evidence": match.group(0).strip()[:240],
            }
    return {}


def _amount(value: str) -> int:
    compact = re.sub(r"[\s,]", "", value)
    multiplier = 1000 if compact.lower().endswith("k") else 1
    compact = compact.rstrip("kK")
    return round(float(compact) * multiplier)


def _annualize(minimum: int, maximum: int, period: str) -> tuple[int, int]:
    period = re.sub(r"^(?:per\s+|/\s*)", "", period).strip()
    if period in {"hour", "hourly", "hr"}:
        return 0, 0
    if period in {"month", "monthly"}:
        return minimum * 12, maximum * 12
    return minimum, maximum


def _valid_range(minimum: int, maximum: int) -> bool:
    return 10_000 <= minimum <= maximum <= 10_000_000


def _currency_code(value: str) -> str:
    normalized = value.upper() if value.isalpha() else value
    return _CURRENCY_CODES.get(normalized, _CURRENCY_CODES.get(value, "USD"))
