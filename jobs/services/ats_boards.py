"""Check imported listings against the employer's own applicant tracking system.

Aggregators keep listings up long after the employer has closed them, and their
"remote" labels are loose. The employer's ATS is the source of truth for both, so
we parse the application URL, fetch the employer's public job board once per
board (or the posting itself for Workday/SmartRecruiters) and record:

    open     - the posting is live on the employer's board
    closed   - the board answered and the posting is not on it
    unknown  - we could not get a definitive answer (network error, odd response)

plus the employer's own workplace type (remote / hybrid / onsite) where the ATS
exposes it. Callers decide what to do with the result.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 20
HEADERS = {
    "User-Agent": "RemoteImpactBot/1.0 (+https://remoteimpact.org; job availability check)",
    "Accept": "application/json, text/xml;q=0.9, */*;q=0.5",
}
# ATSs where we fetch one board and check membership.
BOARD_ATS = {"greenhouse", "lever", "ashby", "workable", "bamboohr", "personio", "recruitee", "breezy"}
# ATSs where we ask about each posting directly.
POSTING_ATS = {"workday", "smartrecruiters"}
# A missing board on these ATSs means the employer no longer uses it, so its postings are gone.
BOARD_GONE_MEANS_CLOSED = {"greenhouse", "lever", "ashby"}

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


@dataclass(frozen=True)
class AtsRef:
    ats: str
    board: str  # board token / company / account / host, depending on the ATS
    job: str  # the ATS's own posting id (or slug for Recruitee, path for Workday)
    host: str = ""

    @property
    def board_key(self) -> tuple[str, str, str]:
        return (self.ats, self.board.lower(), self.host)

    def as_dict(self) -> dict:
        return {"ats": self.ats, "board": self.board, "job": self.job, "host": self.host}


@dataclass
class Posting:
    workplace: Optional[str] = None  # "remote" | "hybrid" | "onsite" | None (not stated)
    location: str = ""


@dataclass
class BoardResult:
    status: str  # "ok" | "gone" | "error"
    postings: dict[str, Posting] = field(default_factory=dict)
    error: str = ""


@dataclass
class Check:
    status: str  # "open" | "closed" | "unknown"
    workplace: Optional[str] = None
    location: str = ""
    reason: str = ""


def parse(url: str, greenhouse_boards: Optional[dict[str, str]] = None) -> Optional[AtsRef]:
    """Return the ATS reference for an application URL, or None if we can't check it."""
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    host = (parsed.netloc or "").lower().split(":")[0]
    parts = [unquote(p) for p in parsed.path.split("/") if p]
    query = parse_qs(parsed.query)

    if host.endswith("greenhouse.io"):
        if len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit():
            return AtsRef("greenhouse", parts[0], parts[2])
        token = (query.get("token") or [""])[0]
        board = (query.get("for") or [""])[0]
        if token.isdigit() and board:
            return AtsRef("greenhouse", board, token)
        return None
    gh_jid = (query.get("gh_jid") or [""])[0]
    if gh_jid.isdigit():
        board = (greenhouse_boards or {}).get(host)
        return AtsRef("greenhouse", board, gh_jid) if board else None

    if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        if len(parts) >= 2 and re.fullmatch(UUID, parts[1], re.I):
            return AtsRef("lever", parts[0], parts[1].lower(), host="eu" if ".eu." in host else "")
        return None

    if host == "jobs.ashbyhq.com":
        if len(parts) >= 2 and re.fullmatch(UUID, parts[1], re.I):
            return AtsRef("ashby", parts[0], parts[1].lower())
        return None

    if host == "apply.workable.com":
        if len(parts) >= 3 and parts[1] == "j":
            return AtsRef("workable", parts[0], parts[2].upper())
        if len(parts) >= 2 and parts[0] == "j":
            return AtsRef("workable", "", parts[1].upper())  # account resolved later
        return None
    if host.endswith(".workable.com") and len(parts) >= 2 and parts[0] == "j":
        return AtsRef("workable", host.split(".")[0], parts[1].upper())

    if host.endswith(".bamboohr.com") and len(parts) >= 2 and parts[0] == "careers" and parts[1].isdigit():
        return AtsRef("bamboohr", host.split(".")[0], parts[1], host=host)

    if (".jobs.personio." in host) and len(parts) >= 2 and parts[0] == "job" and parts[1].isdigit():
        return AtsRef("personio", host.split(".")[0], parts[1], host=host)

    if host.endswith(".recruitee.com") and len(parts) >= 2 and parts[0] == "o":
        return AtsRef("recruitee", host.split(".")[0], parts[1], host=host)

    if host.endswith(".breezy.hr") and len(parts) >= 2 and parts[0] == "p":
        match = re.match(r"([0-9a-f]{12})", parts[1])
        if match:
            return AtsRef("breezy", host.split(".")[0], match.group(1), host=host)
        return None

    if host.endswith(".myworkdayjobs.com") or host.endswith(".myworkdaysite.com"):
        if parts and LOCALE.match(parts[0]):
            parts = parts[1:]
        if len(parts) >= 3 and parts[1] == "job":
            tenant = host.split(".")[0]
            return AtsRef("workday", f"{tenant}/{parts[0]}", "/".join(parts[2:]), host=host)
        return None

    if host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"} and len(parts) >= 2:
        match = re.match(r"(\d{6,})", parts[1])
        if match:
            return AtsRef("smartrecruiters", parts[0], match.group(1))
    return None


def _workplace(remote=None, hybrid=None, onsite=None) -> Optional[str]:
    # Some ATSs set several flags at once ("remote or hybrid"); remote-eligible wins.
    if remote:
        return "remote"
    if hybrid:
        return "hybrid"
    if onsite or (remote is False and hybrid is False):
        return "onsite"
    return None


def _label_workplace(value) -> Optional[str]:
    text = str(value or "").lower().replace("-", "").replace("_", "").replace(" ", "")
    if not text:
        return None
    if "hybrid" in text or "flexible" in text:
        return "hybrid"
    if "remote" in text:
        return "remote"
    if "onsite" in text or "office" in text:
        return "onsite"
    return None


def _get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    return session.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)


def fetch_board(ref: AtsRef, session: Optional[requests.Session] = None) -> BoardResult:
    """Fetch every live posting on one employer board."""
    session = session or requests.Session()
    try:
        if ref.ats == "greenhouse":
            resp = _get(session, f"https://boards-api.greenhouse.io/v1/boards/{ref.board}/jobs")
            if resp.status_code == 404:
                return BoardResult("gone")
            resp.raise_for_status()
            jobs = resp.json()["jobs"]
            return BoardResult("ok", {
                str(j["id"]): Posting(None, (j.get("location") or {}).get("name") or "") for j in jobs
            })
        if ref.ats == "lever":
            api = "api.eu.lever.co" if ref.host == "eu" else "api.lever.co"
            resp = _get(session, f"https://{api}/v0/postings/{ref.board}?mode=json")
            if resp.status_code == 404:
                return BoardResult("gone")
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                return BoardResult("error", error="unexpected lever payload")
            return BoardResult("ok", {
                str(j["id"]).lower(): Posting(
                    _label_workplace(j.get("workplaceType")),
                    (j.get("categories") or {}).get("location") or "",
                ) for j in data
            })
        if ref.ats == "ashby":
            resp = _get(session, f"https://api.ashbyhq.com/posting-api/job-board/{ref.board}")
            if resp.status_code == 404:
                return BoardResult("gone")
            resp.raise_for_status()
            jobs = resp.json()["jobs"]
            return BoardResult("ok", {
                str(j["id"]).lower(): Posting(
                    _label_workplace(j.get("workplaceType")) or ("remote" if j.get("isRemote") else None),
                    j.get("location") or "",
                ) for j in jobs
            })
        if ref.ats == "workable":
            resp = _get(session, f"https://apply.workable.com/api/v1/widget/accounts/{ref.board}")
            if resp.status_code == 404:
                return BoardResult("error", error="workable account not found")
            resp.raise_for_status()
            jobs = resp.json()["jobs"]
            return BoardResult("ok", {
                str(j["shortcode"]).upper(): Posting(
                    "remote" if j.get("telecommuting") else None,
                    ", ".join(filter(None, [j.get("city"), j.get("country")])),
                ) for j in jobs
            })
        if ref.ats == "bamboohr":
            resp = _get(session, f"https://{ref.host}/careers/list")
            resp.raise_for_status()
            jobs = resp.json()["result"]
            types = {"0": "onsite", "1": "remote", "2": "hybrid"}
            return BoardResult("ok", {
                str(j["id"]): Posting(
                    types.get(str(j.get("locationType"))) or ("remote" if j.get("isRemote") else None),
                    ", ".join(filter(None, [(j.get("location") or {}).get("city"), (j.get("location") or {}).get("state")])),
                ) for j in jobs
            })
        if ref.ats == "personio":
            resp = _get(session, f"https://{ref.host}/xml")
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            if root.tag != "workzag-jobs":
                return BoardResult("error", error="unexpected personio payload")
            return BoardResult("ok", {
                (pos.findtext("id") or "").strip(): Posting(None, (pos.findtext("office") or "").strip())
                for pos in root.findall("position")
            })
        if ref.ats == "recruitee":
            resp = _get(session, f"https://{ref.host}/api/offers/")
            resp.raise_for_status()
            offers = resp.json()["offers"]
            return BoardResult("ok", {
                str(o["slug"]): Posting(
                    _workplace(o.get("remote"), o.get("hybrid"), o.get("on_site")), o.get("location") or ""
                ) for o in offers if o.get("status", "published") == "published"
            })
        if ref.ats == "breezy":
            resp = _get(session, f"https://{ref.host}/json")
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                return BoardResult("error", error="unexpected breezy payload")
            postings = {}
            for j in data:
                loc = j.get("location") or {}
                details = _label_workplace((loc.get("remote_details") or {}).get("value"))
                remote = loc.get("is_remote")
                workplace = details or ("remote" if remote else "onsite" if remote is False else None)
                postings[str(j["id"])[:12]] = Posting(workplace, loc.get("name") or "")
            return BoardResult("ok", postings)
    except (requests.RequestException, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        return BoardResult("error", error=f"{type(exc).__name__}: {exc}"[:200])
    return BoardResult("error", error=f"unsupported ats {ref.ats}")


def resolve_workable_account(ref: AtsRef, session: Optional[requests.Session] = None) -> Optional[str]:
    """apply.workable.com/j/CODE redirects to /{account}/j/CODE while the job is published."""
    session = session or requests.Session()
    try:
        resp = session.get(f"https://apply.workable.com/j/{ref.job}", headers=HEADERS, timeout=TIMEOUT,
                           allow_redirects=True)
    except requests.RequestException:
        return None
    parts = [p for p in urlparse(resp.url).path.split("/") if p]
    if len(parts) >= 3 and parts[1] == "j":
        return parts[0]
    return None


def check_posting(ref: AtsRef, session: Optional[requests.Session] = None) -> Check:
    """Ask the ATS about one posting (Workday, SmartRecruiters)."""
    session = session or requests.Session()
    try:
        if ref.ats == "smartrecruiters":
            resp = _get(session, f"https://api.smartrecruiters.com/v1/companies/{ref.board}/postings/{ref.job}")
            if resp.status_code == 404:
                return Check("closed", reason="posting not found")
            if resp.status_code != 200:
                return Check("unknown", reason=f"http {resp.status_code}")
            data = resp.json()
            loc = data.get("location") or {}
            if data.get("active") is False:
                return Check("closed", reason="posting inactive")
            return Check("open", _workplace(loc.get("remote"), loc.get("hybrid")), loc.get("fullLocation") or "")
        if ref.ats == "workday":
            tenant, site = ref.board.split("/", 1)
            base = f"https://{ref.host}/wday/cxs/{tenant}/{site}"
            resp = _get(session, f"{base}/job/{ref.job}")
            if resp.status_code == 200:
                info = resp.json().get("jobPostingInfo") or {}
                if info.get("posted") is False or info.get("canApply") is False:
                    return Check("closed", reason="posting not accepting applications")
                return Check("open", _label_workplace(info.get("remoteType")), info.get("location") or "")
            if resp.status_code not in (403, 404):
                return Check("unknown", reason=f"http {resp.status_code}")
            # 404 / 403 "S22" are what Workday returns for unposted requisitions. Confirm by
            # searching the board for the requisition id before calling it closed.
            req = re.search(r"_((?:JR|R|REQ)?-?\d{3,}[\w-]*)$", ref.job)
            if not req:
                return Check("unknown", reason=f"http {resp.status_code}, no requisition id")
            search = session.post(f"{base}/jobs", headers={**HEADERS, "Content-Type": "application/json"},
                                  json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": req.group(1)},
                                  timeout=TIMEOUT)
            if search.status_code != 200:
                return Check("unknown", reason=f"search http {search.status_code}")
            postings = search.json().get("jobPostings") or []
            slug = ref.job.rsplit("/", 1)[-1]
            if any((p.get("externalPath") or "").rstrip("/").endswith(slug) for p in postings):
                return Check("open")
            return Check("closed", reason="requisition not on board")
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        return Check("unknown", reason=f"{type(exc).__name__}: {exc}"[:200])
    return Check("unknown", reason=f"unsupported ats {ref.ats}")


REMOTE_LOCATION = re.compile(r"\b(remote|anywhere|worldwide|work from home|home office|home-based)\b", re.I)


def excludes_remote(check: Check) -> bool:
    """True when the employer's own board says the role needs an office.

    Hybrid postings whose location still says remote ("United Kingdom - Remote") are
    treated as remote-eligible; only a clear on-site/hybrid answer takes a role down.
    """
    if check.status != "open" or check.workplace not in {"onsite", "hybrid"}:
        return False
    return not REMOTE_LOCATION.search(check.location or "")


def check_against_board(ref: AtsRef, board: BoardResult) -> Check:
    if board.status == "gone":
        if ref.ats in BOARD_GONE_MEANS_CLOSED:
            return Check("closed", reason="employer board no longer exists")
        return Check("unknown", reason="board not found")
    if board.status != "ok":
        return Check("unknown", reason=board.error or "board error")
    posting = board.postings.get(ref.job)
    if posting is None:
        return Check("closed", reason="not on employer board")
    return Check("open", posting.workplace, posting.location)


# --- Full listings for direct ingestion -------------------------------------------------

LISTING_ATS = {"greenhouse", "lever", "ashby", "workable", "recruitee", "personio", "bamboohr"}
BAMBOO_DETAIL_CAP = 40  # BambooHR needs one request per posting for its description


@dataclass
class Listing:
    job: str
    title: str
    url: str
    description_html: str
    location: str
    workplace: Optional[str]
    posted_at: Optional[str] = None  # ISO timestamp
    employment_type: str = ""
    salary_text: str = ""


def is_remote_listing(listing: Listing) -> bool:
    """Remote per the employer: an explicit remote flag, or no flag and a remote location."""
    if listing.workplace == "remote":
        return True
    if listing.workplace in {"onsite", "hybrid"}:
        return False
    return bool(REMOTE_LOCATION.search(listing.location or ""))


def fetch_listings(ref: AtsRef, session: Optional[requests.Session] = None) -> Optional[list[Listing]]:
    """Every live posting on a board with its description, or None if the board didn't answer."""
    session = session or requests.Session()
    try:
        if ref.ats == "greenhouse":
            resp = _get(session, f"https://boards-api.greenhouse.io/v1/boards/{ref.board}/jobs?content=true")
            resp.raise_for_status()
            import html as html_lib
            return [
                Listing(
                    job=str(j["id"]), title=j.get("title") or "", url=j.get("absolute_url") or "",
                    description_html=html_lib.unescape(j.get("content") or ""),
                    location=(j.get("location") or {}).get("name") or "", workplace=None,
                    posted_at=j.get("first_published") or j.get("updated_at"),
                )
                for j in resp.json()["jobs"]
            ]
        if ref.ats == "lever":
            api = "api.eu.lever.co" if ref.host == "eu" else "api.lever.co"
            resp = _get(session, f"https://{api}/v0/postings/{ref.board}?mode=json")
            resp.raise_for_status()
            listings = []
            for j in resp.json():
                parts = [j.get("description") or ""]
                for block in j.get("lists") or []:
                    parts.append(f"<h3>{block.get('text', '')}</h3><ul>{block.get('content', '')}</ul>")
                parts.append(j.get("additional") or "")
                salary = j.get("salaryRange") or {}
                created = j.get("createdAt")
                listings.append(Listing(
                    job=str(j["id"]).lower(), title=j.get("text") or "", url=j.get("hostedUrl") or "",
                    description_html="\n".join(parts),
                    location=(j.get("categories") or {}).get("location") or "",
                    workplace=_label_workplace(j.get("workplaceType")),
                    posted_at=_ms_to_iso(created) if created else None,
                    employment_type=(j.get("categories") or {}).get("commitment") or "",
                    salary_text=(f"{salary.get('currency', '')} {salary.get('min', '')} - {salary.get('max', '')}"
                                 if salary.get("min") else ""),
                ))
            return listings
        if ref.ats == "ashby":
            resp = _get(session, f"https://api.ashbyhq.com/posting-api/job-board/{ref.board}?includeCompensation=true")
            resp.raise_for_status()
            listings = []
            for j in resp.json()["jobs"]:
                if j.get("isListed") is False:
                    continue
                # isRemote is a legacy flag that is also set on office roles; trust workplaceType,
                # and otherwise fall back to the location text.
                workplace = _label_workplace(j.get("workplaceType"))
                listings.append(Listing(
                    job=str(j["id"]).lower(), title=j.get("title") or "", url=j.get("jobUrl") or "",
                    description_html=j.get("descriptionHtml") or "", location=j.get("location") or "",
                    workplace=workplace, posted_at=j.get("publishedAt"),
                    employment_type=j.get("employmentType") or "",
                    salary_text=(j.get("compensation") or {}).get("compensationTierSummary") or "",
                ))
            return listings
        if ref.ats == "workable":
            if not ref.board:
                return None
            resp = _get(session, f"https://apply.workable.com/api/v1/widget/accounts/{ref.board}?details=true")
            resp.raise_for_status()
            listings = []
            for j in resp.json()["jobs"]:
                place = ", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")]))
                listings.append(Listing(
                    job=str(j["shortcode"]).upper(), title=j.get("title") or "",
                    url=j.get("url") or j.get("shortlink") or "", description_html=j.get("description") or "",
                    location=("Remote" + (f" ({place})" if place else "")) if j.get("telecommuting") else place,
                    workplace="remote" if j.get("telecommuting") else None,
                    posted_at=j.get("published_on") or j.get("created_at"),
                    employment_type=j.get("employment_type") or "",
                ))
            return listings
        if ref.ats == "recruitee":
            resp = _get(session, f"https://{ref.host}/api/offers/")
            resp.raise_for_status()
            listings = []
            for o in resp.json()["offers"]:
                if o.get("status", "published") != "published":
                    continue
                listings.append(Listing(
                    job=str(o["slug"]), title=o.get("title") or "", url=o.get("careers_url") or "",
                    description_html=(o.get("description") or "") + (o.get("requirements") or ""),
                    location=("Remote" if o.get("remote") else "") or o.get("location") or "",
                    workplace=_workplace(o.get("remote"), o.get("hybrid"), o.get("on_site")),
                    posted_at=(o.get("published_at") or "").replace(" UTC", "+00:00").replace(" ", "T", 1) or None,
                    employment_type=o.get("employment_type_code") or "",
                ))
            return listings
        if ref.ats == "personio":
            resp = _get(session, f"https://{ref.host}/xml")
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            listings = []
            for pos in root.findall("position"):
                job_id = (pos.findtext("id") or "").strip()
                parts = []
                for block in pos.iter("jobDescription"):
                    parts.append(f"<h3>{block.findtext('name') or ''}</h3>{block.findtext('value') or ''}")
                office = (pos.findtext("office") or "").strip()
                listings.append(Listing(
                    job=job_id, title=(pos.findtext("name") or "").strip(),
                    url=f"https://{ref.host}/job/{job_id}", description_html="".join(parts),
                    location=office, workplace=None, posted_at=pos.findtext("createdAt"),
                    employment_type=pos.findtext("employmentType") or "",
                ))
            return listings
        if ref.ats == "bamboohr":
            resp = _get(session, f"https://{ref.host}/careers/list")
            resp.raise_for_status()
            types = {"0": "onsite", "1": "remote", "2": "hybrid"}
            listings = []
            for j in resp.json()["result"][:BAMBOO_DETAIL_CAP]:
                workplace = types.get(str(j.get("locationType"))) or ("remote" if j.get("isRemote") else None)
                if workplace in {"onsite", "hybrid"}:
                    continue  # skip the per-posting request for roles we would not import
                detail = _get(session, f"https://{ref.host}/careers/{j['id']}/detail")
                if detail.status_code != 200:
                    continue
                info = (detail.json().get("result") or {}).get("jobOpening") or {}
                loc = info.get("location") or {}
                listings.append(Listing(
                    job=str(j["id"]), title=info.get("jobOpeningName") or j.get("jobOpeningName") or "",
                    url=info.get("jobOpeningShareUrl") or f"https://{ref.host}/careers/{j['id']}",
                    description_html=info.get("description") or "",
                    location=("Remote" if workplace == "remote" else "") or ", ".join(filter(None, [loc.get("city"), loc.get("state")])),
                    workplace=workplace, posted_at=info.get("datePosted"),
                    employment_type=info.get("employmentStatusLabel") or "",
                    salary_text=str(info.get("compensation") or ""),
                ))
            return listings
    except (requests.RequestException, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        logger.warning("Listing fetch failed for %s/%s: %s", ref.ats, ref.board, exc)
        return None
    return None


def _ms_to_iso(ms) -> Optional[str]:
    from datetime import datetime, timezone as dt_timezone
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=dt_timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None
