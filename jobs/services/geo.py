"""Small, conservative geography helpers shared by importers and cleanup."""
import re

from django_countries import countries

from jobs.services.scout_constraints import location_requests, place_patterns, state_names

DISPLAY = {"US": "USA", "GB": "UK"}
# Two-letter codes that are both a US state and a country (CA, IN, PA...).
US_STATE_CODES = set("AL AR CA CO DE GA ID IL IN LA MA MD ME MN MO MT NC NE PA SC SD TN VA".split())


def single_country(text):
    """The one country a text names, or None if zero or several."""
    text = str(text or "")
    members, _ = place_patterns()
    codes = set()
    for name in location_requests(text):
        found = members.get(name, set())
        if len(found) != 1:  # a region such as "Europe" is not a single country
            return None
        codes |= found
    if state_names(text):
        codes.add("US")
    if not codes:
        # "Zürich, ZH, CH" / "Manila, NCR, PH": a trailing ISO country code.
        trailing = re.search(r",\s*([A-Z]{2})\s*$", text)
        if trailing and trailing.group(1) in dict(countries) and trailing.group(1) not in US_STATE_CODES:
            codes.add(trailing.group(1))
    if len(codes) != 1:
        return None
    code = codes.pop()
    return DISPLAY.get(code, dict(countries).get(code))
