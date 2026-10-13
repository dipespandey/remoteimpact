"""Page-specific search titles and concise meta descriptions."""
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


def job_title_tag(title: str, organization: str, suffix: str = "Remote Impact") -> str:
    """Keep role and employer identity; omit optional branding on longer titles.

    Search result width is a display heuristic, not a reason to remove the
    words that distinguish one posting from another.
    """
    title = " ".join((title or "").split())
    organization = " ".join((organization or "").split())
    # Feeds sometimes append the employer to the role itself. Repeating it in
    # the title tag crowds out the actual role and can force needless trimming.
    if organization:
        role = re.sub(r"\s+(?:at|for)\s+" + re.escape(organization) + r"$", "", title, flags=re.I)
        if role.strip():
            title = role.strip()
    base = f"{title} at {organization}"
    full = f"{base} | {suffix}"
    if len(full) <= TITLE_LIMIT:
        return full
    return base


def job_meta_description(title: str, organization: str, location: str, job_type: str, pay: str, description_html: str) -> str:
    facts = ", ".join(p for p in (location or "Remote", (job_type or "").lower(), pay) if p)
    head = f"{title} at {organization}. {facts}. "
    summary = " ".join(strip_tags(description_html or "").split())
    room = DESCRIPTION_LIMIT - len(head)
    if room < 30:
        return _trim(head.rstrip(), DESCRIPTION_LIMIT)
    return head + _trim(summary, room) if summary else _trim(head.rstrip(), DESCRIPTION_LIMIT)
