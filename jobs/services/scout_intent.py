"""Deterministic boundaries for explicit wanted roles, distinct from a CV."""
import re

# Prefer an explicit list even when it occurs after the introductory sentence.
# Never scan qualifications/history for plausible titles.
ROLE_LIST = re.compile(
    r'\b(?:search for|prioriti[sz]e|looking for|seeking)\s+'
    r'(?P<roles>[^.!?;]+)', re.I,
)


def explicit_role_lists(text):
    result = []
    for match in ROLE_LIST.finditer(text):
        value = re.split(r'\b(?:include|exclude|my qualifications|my background|I have|with experience|skills in)\b',
                         match['roles'], maxsplit=1, flags=re.I)[0].strip()
        if value:
            result.append((match, value))
    # "Search for" and "prioritize" are stronger than an introductory
    # "looking for paid remote jobs" without specific professions.
    return sorted(result, key=lambda row: not re.match(r'search for|prioriti[sz]e', row[0].group(), re.I))


def role_list_parts(value):
    value = re.split(r'\b(?:roles?|jobs?|positions?)\b|\b(?:in|at|using|with|paying)\b', value, maxsplit=1, flags=re.I)[0]
    return [part.strip() for part in re.split(r',|\bor\b|\band\b|\|', value, flags=re.I) if part.strip()]


def sector_request_text(text):
    """Remove role alternatives, retaining a following explicit sector request."""
    # Education Support names a function, unlike a sector qualifier such as
    # "climate engineer" or "roles in education". Retain those qualifiers.
    return re.sub(r'\beducation\s+(?:(?:programme?|project)\s+)?support\b', ' ', text, flags=re.I)


SOFT = re.compile(r'\b(?:prefer(?:red|ably)?|ideally|ideally suited|prioriti[sz]e|interested in|focus on)\b', re.I)
HARD = re.compile(r'\b(?:only|exclusively|must|mandatory|non[- ]negotiable|required)\b', re.I)
EMPLOYER_ALTERNATIVE = r'(?:private[- ]sector|for[- ]profit|B[- ]?Corps?|social enterprises?)'


def _intent_clauses(text):
    return re.split(r'[.!?;\n]|,\s*(?=(?:prefer|ideally|prioriti[sz]|interested|focus on)\b)', text, flags=re.I)


def _optional_subject(clause, subjects):
    """A preference for hours/type later in a clause doesn't soften its sector."""
    matches = [m for subject in subjects for m in re.finditer(subject, clause, re.I)]
    if not matches:
        return False
    first = min(matches, key=lambda m: m.start())
    prefix = clause[:first.start()]
    suffix = clause[first.end():].split(',', 1)[0]
    if HARD.search(prefix) or HARD.search(suffix):
        return False
    return bool(SOFT.search(prefix) or re.match(r'\s*(?:(?:employers?|organi[sz]ations?|roles?|jobs?|sector)\s+)?preferred\b', suffix, re.I))


def required_nonprofit(text):
    """Mentioned experience/preferences are not mandatory employer ownership."""
    clauses = _intent_clauses(text)
    mentions = [c for c in clauses if re.search(r'\bnon[- ]?profits?\b', c, re.I)]
    if not mentions:
        return None
    exclusive = [c for c in mentions if re.search(
        r'\b(?:only|exclusively|must be)\s+(?:at |with |in )?(?:a )?non[- ]?profit|'
        r'\bnon[- ]?profit (?:employers?|organi[sz]ations?)\s+only\b', c, re.I)]
    if any(not re.search(EMPLOYER_ALTERNATIVE, c, re.I) for c in exclusive):
        return 'nonprofit'
    # Alternative employer types are explicitly accepted, including in a later
    # sentence; don't reintroduce a nonprofit gate from the earlier sector list.
    if any(re.search(EMPLOYER_ALTERNATIVE, c, re.I) and re.search(
            r'\b(?:open to|accept|both|either|or|and|include)\b', c, re.I) for c in clauses):
        return None
    requested = [c for c in mentions if not (
        _optional_subject(c, [r'\bnon[- ]?profits?\b'])
        or re.search(r'\b(?:my background|my experience|I have|I worked|previously|used to)\b', c, re.I)
           and not re.search(r'\b(?:looking for|seeking|want|roles?|jobs?|employers?)\b', c, re.I)
    )]
    return 'nonprofit' if requested else None


def sector_requirements(text, categories, aliases, hard_categories):
    """Preserve a sector option set without mixing in the job-function category."""
    terms = {slug: [slug.replace('-', ' '), *[p for p, s in aliases.items() if s == slug]]
             for slug in categories}
    subjects = [r'\b' + re.escape(phrase) + r'\b' for slug in categories if slug in hard_categories for phrase in terms[slug]]
    clauses = [c for c in _intent_clauses(text) if not (
        _optional_subject(c, subjects)
        or re.search(r'\b(?:my background|my experience|I have|I worked|previously)\b', c, re.I)
           and not re.search(r'\b(?:looking for|seeking|want|roles?|jobs?|employers?)\b', c, re.I)
    )]
    hard_text = ' '.join(clauses)
    def present(value, slug):
        return any(re.search(r'\b' + re.escape(phrase) + r'\b', value, re.I)
                   for phrase in terms[slug])
    required = [slug for slug in categories if slug in hard_categories and present(hard_text, slug)]
    for clause in clauses:
        match = re.search(
            r'\b(?:jobs?|roles?|work|positions?|organi[sz]ations?|employers?|sectors?|fields?)\b'
            r'[^.!?;]{0,65}?\b(?:in|working in|focused on)\s+(.+)', clause, re.I)
        if not match:
            match = re.search(r'\b(?:impact sectors|sectors?|organi[sz]ations?)\s*[:—–]\s*(.+)', clause, re.I)
        if not match:
            continue
        value = match[1]
        options = [slug for slug in categories if present(value, slug)]
        # Health is a sector in an explicit sector list, not a health-insurance
        # benefit or an invented global-health requirement elsewhere in a CV.
        if 'global-health' in categories and re.search(r'\bhealth\b', value, re.I) and 'global-health' not in options:
            options.append('global-health')
        if len(options) > 1 and re.search(r',|\bor\b', value, re.I):
            return required or [slug for slug in options if slug in hard_categories], options
    return required, []
