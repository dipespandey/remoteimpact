"""Each employer's own homepage and careers page.

Careers pages come from the applicant-tracking board their live roles are posted on. Homepages come from the
website on record, a verified claim's email domain, or the domain their own application links use, and are only
kept when the domain plausibly belongs to the organization (it carries a word of its name or its acronym) and the
page loads. Job boards, profile pages and ATS domains are never a homepage.
"""
import re
from collections import Counter
from urllib.parse import urlparse

from jobs.services import ats_boards

NOT_EMPLOYER_HOSTS = (
    "idealist.org", "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "recruitee.com", "personio.de", "personio.com",
    "bamboohr.com", "smartrecruiters.com", "myworkdayjobs.com", "workday.com", "breezy.hr", "climatebase.org",
    "reliefweb.int", "probablygood.org", "remoteimpact.org", "devex.com", "impactpool.org", "wellfound.com",
    "indeed.com", "glassdoor.com", "applytojob.com", "paylocity.com", "icims.com", "rippling.com", "jobvite.com",
    "taleo.net", "ultipro.com", "adp.com", "teamtailor.com", "dover.com", "pinpointhq.com", "zohorecruit.com",
    "airtable.com", "google.com", "forms.gle", "typeform.com", "sharepoint.com", "notion.site", "notion.so",
    "jotform.com", "hirehive.com", "careers-page.com", "gusto.com", "jobs.ac.uk", "gmail.com",
    "outlook.com", "hotmail.com", "yahoo.com", "proton.me", "protonmail.com",
)
STOP_WORDS = {
    "the", "and", "for", "of", "inc", "llc", "ltd", "gmbh", "foundation", "institute", "international", "global",
    "group", "network", "center", "centre", "project", "initiative", "fund", "trust", "association", "society",
    "council", "alliance", "organization", "organisation", "company", "corporation", "corp", "university", "labs",
    "lab", "partners", "partnership", "coalition", "campaign", "program", "programme", "services", "world",
}


def host_of(url: str) -> str:
    return urlparse(url if "//" in (url or "") else f"//{url or ''}").netloc.lower().split(":")[0].removeprefix("www.")


def employer_host(host: str) -> bool:
    return bool(host) and "." in host and not any(host == d or host.endswith("." + d) for d in NOT_EMPLOYER_HOSTS)


def name_tokens(name: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
    tokens = {w for w in words if len(w) >= 4 and w not in STOP_WORDS}
    initials = "".join(w[0] for w in words if w not in {"the", "of", "and", "for"})
    if len(initials) >= 3:
        tokens.add(initials)
    acronym = re.search(r"\(([A-Za-z]{2,8})\)", name or "")
    if acronym:
        tokens.add(acronym.group(1).lower())
    compact = "".join(words)
    if 4 <= len(compact) <= 30:
        tokens.add(compact)
    return tokens


def domain_matches(host: str, name: str) -> bool:
    """The registrable part of the domain carries a word of the organization's name, or its acronym."""
    labels = host.split(".")
    core = "".join(labels[-3:-1]) if len(labels) >= 3 and len(labels[-2]) <= 3 else labels[-2] if len(labels) >= 2 else host
    core = core.replace("-", "")
    return any(token in core or (len(core) >= 4 and core in token) for token in name_tokens(name))


def board_url(ref) -> str:
    """The public board page for an ATS reference, where one exists."""
    board = ref.board
    if not board:
        return ""
    return {
        "greenhouse": f"https://job-boards.greenhouse.io/{board}",
        "lever": f"https://jobs.{'eu.' if ref.host == 'eu' else ''}lever.co/{board}",
        "ashby": f"https://jobs.ashbyhq.com/{board}",
        "workable": f"https://apply.workable.com/{board}/",
        "bamboohr": f"https://{ref.host or board + '.bamboohr.com'}/careers",
        "personio": f"https://{ref.host}/" if ref.host else "",
        "recruitee": f"https://{ref.host}/" if ref.host else "",
        "breezy": f"https://{ref.host}/" if ref.host else "",
    }.get(ref.ats, "")


def careers_url_for(application_urls) -> str:
    """The board most of an employer's live roles are posted on."""
    boards = Counter()
    for url in application_urls:
        ref = ats_boards.parse(url)
        if ref:
            page = board_url(ref)
            if page:
                boards[page] += 1
    return boards.most_common(1)[0][0] if boards else ""


def homepage_candidates(org, application_urls, claim_domains=()):
    """(url, source) pairs to try, most trusted first."""
    found = []
    host = host_of(org.website)
    if org.website and employer_host(host):
        found.append((f"https://{host}/", "website"))
    for domain in claim_domains:
        if employer_host(domain) and domain_matches(domain, org.name):
            found.append((f"https://{domain}/", "claim"))
    hosts = Counter(host_of(u) for u in application_urls if u)
    for apply_host, _ in hosts.most_common(3):
        root = ".".join(apply_host.split(".")[-3:]) if apply_host.count(".") >= 2 and len(apply_host.split(".")[-2]) <= 3 else ".".join(apply_host.split(".")[-2:])
        if employer_host(root) and domain_matches(root, org.name):
            found.append((f"https://{root}/", "apply_domain"))
    return list(dict.fromkeys(found))


def resolve_homepage(org, application_urls, claim_domains=(), fetch=None):
    """(url, source) of the first candidate that loads, or ("", "")."""
    if fetch is None:
        from jobs.services.safe_fetch import fetch_public_html

        def fetch(url):
            return fetch_public_html(url, max_bytes=800_000, total_timeout=12).status == "ok"
    for url, source in homepage_candidates(org, application_urls, claim_domains):
        if fetch(url):
            return url, source
    return "", ""


GUESS_TLDS = ("org", "com", "io", "net", "ai", "earth", "co")
PARKED = re.compile(
    r"domain (?:name )?(?:is |may be )?for sale|buy this domain|this domain (?:is|may be) (?:for sale|available)|"
    r"make an offer|domain parking|parked (?:free|domain)|hugedomains|(?<![\w.-])dan\.com\b|sedo(?:parking)?\b|afternic|"
    r"godaddy\.com/domainsearch|is available for purchase",
    re.I,
)


def looks_parked(html: str) -> bool:
    return bool(PARKED.search((html or "")[:200_000]))


def guessed_domains(org, careers_url: str = "") -> list[str]:
    """Likely domains from the ATS board name and the organization's compact name ("givedirectly.org")."""
    stems = []
    path = urlparse(careers_url or "").path.strip("/").split("/")
    if careers_url and path and path[0]:
        stems.append(path[0].lower())
    elif careers_url:
        stems.append(host_of(careers_url).split(".")[0])
    words = re.findall(r"[a-z0-9]+", (org.name or "").lower())
    compact = "".join(w for w in words if w not in {"the", "inc", "llc", "ltd"})
    if 4 <= len(compact) <= 30:
        stems.append(compact)
    stems = [re.sub(r"[^a-z0-9-]", "", stem) for stem in dict.fromkeys(stems) if len(stem) >= 4]
    return [f"{stem}.{tld}" for stem in stems for tld in GUESS_TLDS][:10]


def page_names_org(html: str, name: str) -> bool:
    """The page's title or site name carries a word of the organization's name."""
    title = " ".join(re.findall(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)[:1])
    site = " ".join(re.findall(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)', html or "", re.I)[:1])
    text = re.sub(r"[^a-z0-9]+", "", (title + " " + site).lower())
    return any(token in text for token in name_tokens(name) if len(token) >= 4)


def name_words(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(w) >= 3 and w not in STOP_WORDS | {"llc", "inc", "ltd"}]


def page_names_org_fully(html: str, name: str) -> bool:
    """Every significant word of the name is in the page's title or site name (for guessed domains)."""
    title = " ".join(re.findall(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)[:1])
    site = " ".join(re.findall(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)', html or "", re.I)[:1])
    text = re.sub(r"[^a-z0-9]+", "", (title + " " + site).lower())
    words = name_words(name)
    return bool(words) and all(word in text for word in words)


def guess_homepage(org, careers_url: str = "", fetch_html=None) -> str:
    if fetch_html is None:
        from jobs.services.safe_fetch import fetch_public_html

        def fetch_html(url):
            result = fetch_public_html(url, max_bytes=600_000, total_timeout=8, max_redirects=2)
            return result.text if result.status == "ok" else None
    for domain in guessed_domains(org, careers_url):
        if not employer_host(domain) or not domain_matches(domain, org.name):
            continue
        # A one-word name ("Slipstream", "Archer") names many organizations; leave those to research.
        if len(name_words(org.name)) < 2:
            continue
        html = fetch_html(f"https://{domain}/")
        if html and not looks_parked(html) and page_names_org_fully(html, org.name):
            return f"https://{domain}/"
    return ""
