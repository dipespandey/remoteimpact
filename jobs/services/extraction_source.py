"""Lossless source preparation and compact evidence references for extraction."""
import re
from html.parser import HTMLParser
from jobs.services.job_facts import plain


class _SourceText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        if not self.hidden and tag in {'p', 'li', 'div', 'br', 'h1', 'h2', 'h3', 'tr', 'section'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {'p', 'li', 'div', 'h1', 'h2', 'h3', 'tr', 'section'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def compact_source(text):
    parser = _SourceText()
    parser.feed(str(text or ''))
    parser.close()
    lines = [' '.join(line.split()) for line in ''.join(parser.parts).splitlines()]
    seen, kept = set(), []
    for line in lines:
        if not line:
            continue
        # Repeated headings can change the meaning of the paragraphs below them.
        # Deduplicate only substantial identical paragraphs, never short labels.
        if len(line) >= 80 and line in seen:
            continue
        kept.append(line)
        seen.add(line)
    return '\n'.join(kept)


def evidence_source(title, organization, description):
    parts = [plain(title), plain(organization), compact_source(description)]
    spans = []
    for part in parts:
        spans.extend(value.strip() for value in re.split(r'\n+|(?<=[.!?;])\s+(?=[A-Z0-9])', part) if value.strip())
    return {f'S{index}': value for index, value in enumerate(spans)}


def resolve_evidence(data, spans):
    """Unknown references fail closed; never interpret a generated quote as an ID."""
    if isinstance(data, list):
        return [resolve_evidence(value, spans) for value in data]
    if not isinstance(data, dict):
        return data
    resolved = {}
    for key, value in data.items():
        if key == 'evidence':
            if isinstance(value, str):
                resolved[key] = spans.get(value, '')
            elif isinstance(value, list) and value and all(isinstance(ref, str) and ref in spans for ref in value):
                quotes = [spans[ref] for ref in dict.fromkeys(value)]
                resolved[key] = quotes[0] if len(quotes) == 1 else quotes
            else:
                resolved[key] = ''
        else:
            resolved[key] = resolve_evidence(value, spans)
    return resolved
