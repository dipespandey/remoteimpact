"""Titles and meta descriptions that fit in search results (about 60 and 155 characters)."""
import re
import json

from django.utils.html import strip_tags

TITLE_LIMIT = 62
DESCRIPTION_LIMIT = 155


def json_ld(data) -> str:
    """Serialize structured data without allowing source text to close a script."""
    return json.dumps(data, ensure_ascii=False).replace('<', r'\u003c').replace('>', r'\u003e').replace('&', r'\u0026')


def _trim(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return cut + "…"


def _trim_role(text: str, limit: int) -> str:
    """Keep a role's leading name and trailing region/specialization distinct."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    front_budget = max(1, int((limit - 3) * 0.6))
    front = text[:front_budget].rsplit(" ", 1)[0].rstrip(" ,;:-–—") or text[:front_budget]
    tail_budget = limit - len(front) - 3
    tail = text[-tail_budget:]
    if len(text) > tail_budget and text[-tail_budget - 1] != " ":
        tail = tail.split(" ", 1)[-1]
    tail = tail.lstrip(" ,;:-–—")
    return f"{front} … {tail}"


def job_title_tag(title: str, organization: str, suffix: str = "Remote Impact") -> str:
    """"Role at Employer | Remote Impact" when it fits; otherwise the role and employer, trimming the longer one."""
    base = f"{title} at {organization}"
    full = f"{base} | {suffix}"
    if len(full) <= TITLE_LIMIT:
        return full
    if len(base) <= TITLE_LIMIT:
        return base
    room = TITLE_LIMIT - len(" at ")
    # Keep both halves readable: give each up to half, and the unused part of the shorter one to the longer.
    half = room // 2
    if len(organization) <= half:
        title = _trim_role(title, room - len(organization))
    elif len(title) <= half:
        organization = _trim(organization, room - len(title))
    else:
        title, organization = _trim_role(title, half), _trim(organization, room - half)
    return f"{title} at {organization}"


def job_meta_description(title: str, organization: str, location: str, job_type: str, pay: str, description_html: str) -> str:
    facts = ", ".join(p for p in (location or "Remote", (job_type or "").lower(), pay) if p)
    head = f"{title} at {organization}. {facts}. "
    summary = " ".join(strip_tags(description_html or "").split())
    room = DESCRIPTION_LIMIT - len(head)
    if room < 30:
        return _trim(head.rstrip(), DESCRIPTION_LIMIT)
    return head + _trim(summary, room) if summary else _trim(head.rstrip(), DESCRIPTION_LIMIT)
