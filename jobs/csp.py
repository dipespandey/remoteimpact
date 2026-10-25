"""Authorize only explicitly marked, server-authored template JavaScript.

Content hashes survive the anonymous HTML cache. A signature distinguishes an
authored template block from markup supplied through a description or search.
"""
import base64
import hashlib
import hmac
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
import re

from django.conf import settings


def signature(kind, code):
    return hmac.new(settings.SECRET_KEY.encode(),
                    ('remoteimpact-csp-v1:' + kind + ':' + code).encode(), hashlib.sha256).hexdigest()


def digest(code):
    return "'sha256-" + base64.b64encode(hashlib.sha256(code.encode()).digest()).decode() + "'"


class ScriptHashes(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts = set()
        self.handlers = set()
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and not attrs.get('src'):
            self.current = [attrs.get('data-ri-csp', ''), []]
        for name, value in attrs.items():
            if name.startswith('on') and value is not None:
                token = attrs.get('data-ri-csp-' + name, '')
                if hmac.compare_digest(token, signature(name, value)):
                    self.handlers.add(digest(value))

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            token, parts = self.current
            code = ''.join(parts)
            if hmac.compare_digest(token, signature('script', code)):
                self.scripts.add(digest(code))
            self.current = None


@lru_cache(maxsize=1)
def static_fragment_hashes():
    """Permit authored static HTMX fragment handlers before the fragment arrives."""
    scripts, handlers = set(), set()
    for path in (settings.BASE_DIR / 'templates').rglob('*.html'):
        text = path.read_text()
        for attrs, code in re.findall(r'<script\b([^>]*)>(.*?)</script\s*>', text, re.S | re.I):
            if re.search(r'type\s*=\s*[\"\']application/(?:ld\+)?json', attrs, re.I):
                continue
            if '{{' not in code and '{%' not in code:
                scripts.add(digest(code))
        for code in re.findall(r'{%\s*csp_handler\s+"on[a-z]+"\s*%}(.*?){%\s*endcsp_handler\s*%}', text, re.S):
            if '{{' not in code and '{%' not in code:
                from html import unescape
                handlers.add(digest(unescape(code)))
    return scripts, handlers


def policy_hashes(html):
    parser = ScriptHashes()
    parser.feed(html)
    scripts, handlers = static_fragment_hashes()
    return sorted(scripts | parser.scripts), sorted(handlers | parser.handlers)
