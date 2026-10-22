"""Conservative checks for mutually exclusive source residency requirements."""
import re
from jobs.services.job_facts import plain

RESIDENCE = re.compile(
    r'\b(?:applicants?|candidates?|employees?|you)\s+(?:must|need to)\s+'
    r'(?:be\s+)?(?:based|located|reside|live)\s+(?:in|within)\s+'
    r'|\bonly\s+residents?\s+(?:of|in)\s+', re.I,
)


def conflicting_residency(source):
    """Return conflicting positive clauses; never turn them into an OR list.

    Regions, subnational restrictions and conditional alternatives require other
    checks. This guard only establishes contradictions between explicit country
    residence sets, not eligibility from an absence of contradictions.
    """
    from jobs.services.scout_constraints import places, REGIONS, state_names
    source = re.sub(r'\bU\.S\.(?:A\.)?', 'United States', plain(source), flags=re.I)
    clauses = []
    for clause in re.split(r'[.!?;\n]', source):
        match = RESIDENCE.search(clause)
        if not match or re.search(r'\b(?:not|unless|except|excluding|alternatively|either|otherwise)\b', clause, re.I):
            continue
        location = clause[match.end():]
        found = places(location)
        if not found or state_names(location) or any(name in REGIONS for name in found):
            continue
        codes = set().union(*found.values())
        if codes:
            clauses.append((codes, clause.strip()))
    if len(clauses) > 1 and not set.intersection(*(codes for codes, _ in clauses)):
        return [clause for _, clause in clauses]
    return []
