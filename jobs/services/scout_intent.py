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
