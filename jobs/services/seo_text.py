"""Page-specific search titles and concise meta descriptions."""
import re
import json
from html.parser import HTMLParser

from django.utils.html import strip_tags

TITLE_LIMIT = 62
DESCRIPTION_LIMIT = 155


class _DescriptionBlocks(HTMLParser):
    """Keep paragraph and list boundaries from an imported job description."""

    block_tags = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.current = []

    def _flush(self):
        text = " ".join("".join(self.current).split())
        if text:
            self.blocks.append(text)
        self.current = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() in self.block_tags:
            self._flush()

    def handle_endtag(self, tag):
        if tag.casefold() in self.block_tags:
            self._flush()

    def handle_data(self, data):
        self.current.append(data)

    def close(self):
        super().close()
        self._flush()


def _description_blocks(description_html: str) -> list[str]:
    parser = _DescriptionBlocks()
    try:
        parser.feed(description_html or "")
        parser.close()
    except Exception:
        return [" ".join(strip_tags(description_html or "").split())]
    return parser.blocks or [" ".join(strip_tags(description_html or "").split())]


def _role_summary(title: str, description_html: str) -> str:
    """Prefer source text about the role over a reusable employer introduction.

    Many job feeds start with several paragraphs about the employer. A concise
    role-specific block gives searchers a more useful preview and avoids making
    distinct vacancies look identical when their employer boilerplate matches.
    """
    blocks = _description_blocks(description_html)
    title_tokens = [
        token for token in re.findall(r"[a-z0-9]+", (title or "").casefold())
        if len(token) >= 4 and token not in {"senior", "junior", "lead", "role", "full", "time"}
    ]
    # A full distinctive title in a later source block is a strong signal that
    # the employer has moved from company context to the actual vacancy.
    if len(title_tokens) >= 3:
        wanted = set(title_tokens)
        for block in blocks:
            words = set(re.findall(r"[a-z0-9]+", block.casefold()))
            if wanted.issubset(words):
                return block

    responsibility_heading = re.compile(
        r"\b(?:principal\s+|key\s+|core\s+)?(?:duties|responsibilities)(?:\s+(?:and|&|/+)\s+(?:duties|responsibilities))?\s*:?",
        re.IGNORECASE,
    )
    for index, block in enumerate(blocks):
        if not responsibility_heading.search(block):
            continue
        items = [candidate for candidate in blocks[index + 1:index + 5] if len(candidate) >= 24]
        # The first bullet can be shared by different seniority levels. The
        # next concrete responsibility often preserves the distinction without
        # introducing an opaque upstream identifier into a search snippet.
        if len(items) >= 2:
            return items[1]
        if items:
            return items[0]
    return " ".join(blocks)


def json_ld(data) -> str:
    """Serialize structured data without allowing source text to close a script."""
    return json.dumps(data, ensure_ascii=False).replace('<', r'\u003c').replace('>', r'\u003e').replace('&', r'\u0026')


def _trim(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return cut + "…"


def job_title_tag(
    title: str,
    organization: str,
    suffix: str = "Remote Impact",
    *,
    location: str = "",
    disambiguator: str = "",
) -> str:
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
    location = " ".join((location or "").split())
    place_tokens = set(re.findall(r"\w+", location.casefold())) - {"remote", "global", "worldwide", "anywhere"}
    role_tokens = set(re.findall(r"\w+", title.casefold()))
    qualifier = f" — {location}" if place_tokens and not place_tokens.issubset(role_tokens) else ""
    # A catalogue can legitimately contain two current requisitions with the
    # same role, employer, and remote region. Keep those pages discoverable by
    # adding a short, factual qualifier supplied by the view (for example,
    # Entry or Mid) before the employer name.
    disambiguator = " ".join((disambiguator or "").split())
    if disambiguator:
        qualifier += f" ({disambiguator})"
    base = f"{title} at {organization}{qualifier}"
    full = f"{base} | {suffix}"
    if len(full) <= TITLE_LIMIT:
        return full
    return base


def job_meta_description(title: str, organization: str, location: str, job_type: str, pay: str, description_html: str) -> str:
    facts = ", ".join(p for p in (location or "Remote", (job_type or "").lower(), pay) if p)
    head = f"{title} at {organization}. {facts}. "
    summary = _role_summary(title, description_html)
    room = DESCRIPTION_LIMIT - len(head)
    if room < 30:
        return _trim(head.rstrip(), DESCRIPTION_LIMIT)
    return head + _trim(summary, room) if summary else _trim(head.rstrip(), DESCRIPTION_LIMIT)
