"""Remote Impact attribution for navigational links, without changing URL payloads."""
import re
from html import escape, unescape
from html.parser import HTMLParser
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

from django.conf import settings

SOURCE = "remoteimpact"
AGGREGATORS = re.compile(r"^(?:climatebase|idealist|80,?000\s*hours|80000hours|80k|probably\s*good|probablygood|reliefweb|job_board|jobboard|linkedin|indeed|google_jobs)$", re.I)
REFERRAL_KEYS = {"source", "ref", "referrer", "gh_src", "lever-source"}
SIGNED_KEYS = {"signature", "sig", "x-amz-signature", "x-goog-signature"}
FLOW_HOSTS = {"accounts.google.com", "checkout.stripe.com", "billing.stripe.com", "connect.stripe.com"}
ATTR = re.compile(r'''([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?''')


def with_outbound_attribution(url, *, medium="referral", campaign="remote_impact_referrals", internal_hosts=()):
    url = str(url or "")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        own = {"remoteimpact.org", "www.remoteimpact.org", urlsplit(settings.SITE_URL).hostname, *internal_hosts}
        if (parts.scheme.lower() not in {"http", "https", ""} or not host or host in own
                or parts.username or parts.password or host in FLOW_HOSTS):
            return url
        # Provider flows and signed destinations must keep their exact query string.
        path = parts.path.lower()
        if any(segment in path.split("/") for segment in {"oauth", "oauth2", "authorize", "login", "signin", "sso"}):
            return url
        fields = parts.query.split("&") if parts.query else []
        decoded = [(field, unquote_plus(field.partition("=")[0]).lower(), unquote_plus(field.partition("=")[2])) for field in fields]
        if any(key in SIGNED_KEYS for _, key, _ in decoded):
            return url
        lever = host in {"jobs.lever.co", "jobs.eu.lever.co"}
        kept = [field for field, key, value in decoded
                if not key.startswith("utm_")
                and not (key in REFERRAL_KEYS and AGGREGATORS.fullmatch(value.strip()))
                and not (lever and key == "lever-source")]
        tags = {"utm_source": SOURCE, "utm_medium": medium, "utm_campaign": campaign}
        if lever:
            tags["lever-source"] = "RemoteImpact"
        kept.append(urlencode(tags))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment))
    except ValueError:
        return url


def attribute_html_links(html, **options):
    """Edit only anchor href spans; leave scripts, forms and the rest of HTML intact."""
    if "href" not in html.lower():
        return html
    offsets = [0]
    offsets.extend(match.end() for match in re.finditer("\n", html))
    edits = []

    class Anchors(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag != "a" or any(name == "download" for name, _ in attrs):
                return
            raw = self.get_starttag_text()
            for match in ATTR.finditer(raw):
                if match.group(1).lower() != "href":
                    continue
                value = next((value for value in match.groups()[1:] if value is not None), "")
                original = unescape(value)
                tagged = with_outbound_attribution(original, **options)
                if tagged == original:
                    continue
                line, column = self.getpos()
                start = offsets[line - 1] + column
                replacement = 'href="' + escape(tagged, quote=True) + '"'
                edits.append((start + match.start(), start + match.end(), replacement))

        handle_startendtag = handle_starttag

    Anchors(convert_charrefs=False).feed(html)
    if not edits:
        return html
    chunks = []
    cursor = 0
    for start, end, replacement in edits:
        chunks.extend((html[cursor:start], replacement))
        cursor = end
    chunks.append(html[cursor:])
    return "".join(chunks)
