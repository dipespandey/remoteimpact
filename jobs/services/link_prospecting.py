"""Evidence-first prospect research and drafts. No sending or paid APIs."""
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from jobs.link_models import LinkProspect, LinkProspectEvent, canonical_prospect_url
from jobs.services.safe_fetch import fetch_public_html


SEEDS = [
    ("University of Copenhagen — global health careers", "https://globalhealth.ku.dk/career/", "health"),
    ("Ohio University — global health career resources", "https://www.ohio.edu/global-health/career", "health"),
    ("Rutgers — global health career exploration", "https://globalhealth.rutgers.edu/for-students/career-exploration/", "health"),
    ("Duke — global health careers", "https://globalhealth.duke.edu/education/careers-professional-development", "health"),
    ("University at Albany — working in global health", "https://www.albany.edu/globalhealth/working-global-health", "health"),
    ("NIH Fogarty — global health careers", "https://www.fic.nih.gov/Global/Pages/GlobalHealthCareers.aspx", "health"),
]

QUERIES = {
    "climate": ['climate careers "resources" "job boards"', 'sustainability careers "job boards" university'],
    "health": ['global health careers "job boards" university', 'public health career resources remote jobs'],
    "ai": ['AI safety careers resources job boards', 'AI governance career resources fellowship'],
    "impact": ['social impact careers "job boards" resources', 'nonprofit careers resources university remote'],
}


def site_url():
    return settings.SITE_URL.rstrip("/")


def default_target(prospect):
    if prospect.kind == LinkProspect.Kind.RESEARCH:
        return site_url() + "/insights/remote-impact-jobs/"
    if prospect.kind == LinkProspect.Kind.RESOURCE:
        category = {"climate": "climate-environment", "health": "global-health", "ai": "ai-safety"}.get(prospect.sector)
        return site_url() + (f"/jobs/category/{category}/" if category else "/jobs/")
    return site_url() + "/resources/remote-impact-jobs/"


def target_is_ours(url):
    try:
        canonical_prospect_url(url)
        return urlsplit(url).hostname.lower() == urlsplit(site_url()).hostname.lower()
    except (ValidationError, AttributeError, ValueError):
        return False


def comparison_url(url):
    """Ignore fragments and campaign tags, while keeping functional filters."""
    parsed = urlsplit(url)
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                             if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}))
    return (parsed.hostname or "").lower(), parsed.path or "/", query


def inspect_page(prospect, *, placement=False, actor=None, fetched=None):
    url = prospect.placement_url if placement else prospect.url
    if not url:
        raise ValidationError("Record a placement URL first.")
    if placement and not target_is_ours(prospect.target_url):
        raise ValidationError("Choose a Remote Impact target URL before verifying a placement.")
    if placement and target_is_ours(url):
        raise ValidationError("A placement must be on another website.")
    result = fetched if fetched is not None else fetch_public_html(url, total_timeout=8)
    detail = {"url": url, "status": result.status}
    prospect.last_checked_at = timezone.now()
    prospect.check_status = result.status
    prospect.link_evidence = []
    if result.status == "ok":
        soup = BeautifulSoup(result.text, "html.parser")
        base_url = result.final_url or url
        base = soup.find("base", href=True)
        if base:
            try:
                base_url = urljoin(base_url, base["href"])
            except ValueError:
                pass
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        prospect.page_title = soup.title.get_text(" ", strip=True)[:500] if soup.title else ""
        prospect.page_excerpt = soup.get_text(" ", strip=True)[:2500]
        robots = " ".join(tag.get("content", "") for tag in soup.find_all("meta")
                          if tag.get("name", "").lower() in {"robots", "googlebot"})
        headers = {k.lower(): v for k, v in result.headers.items()}
        noindex = "noindex" in (robots + " " + headers.get("x-robots-tag", "")).lower()
        candidates, links = [], []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            try:
                absolute = urljoin(base_url, href)
                parsed = urlsplit(absolute)
            except ValueError:
                continue
            text = anchor.get_text(" ", strip=True)[:250]
            if href.lower().startswith("mailto:"):
                candidates.append({"email": href[7:].split("?")[0][:254], "source_url": result.final_url or url})
            elif (parsed.scheme in {"http", "https"} and parsed.hostname == urlsplit(base_url).hostname
                  and any(word in (href + " " + text).lower() for word in ("contact", "editor", "about", "team"))):
                candidates.append({"url": absolute[:1000], "label": text, "source_url": result.final_url or url})
            if target_is_ours(absolute):
                links.append({"url": absolute[:1000], "anchor": text,
                              "rel": anchor.get("rel", []), "page_noindex": noindex,
                              "matches_target": bool(prospect.target_url) and comparison_url(absolute) == comparison_url(prospect.target_url)})
        if not placement:
            prospect.contact_candidates = candidates[:20]
        prospect.link_evidence = sorted(links, key=lambda link: not link["matches_target"])[:50]
        detail.update(title=prospect.page_title, links=prospect.link_evidence, page_noindex=noindex,
                      links_seen=len(links), links_retained=len(prospect.link_evidence),
                      contact_candidates_seen=len(candidates))
        if placement:
            if target_is_ours(result.final_url or url):
                prospect.check_status = "redirected_to_target_site"
            else:
                prospect.check_status = "target_link_found" if any(link["matches_target"] for link in links) else "target_link_not_found"
    # Failed requests are unknown, never evidence that a link was removed.
    if not placement:
        from .prospect_discovery import assess_page
        prospect.research_assessment = assess_page(result.final_url or url, prospect.sector, result, today=timezone.localdate())
    prospect.save(update_fields=["research_assessment", "last_checked_at", "check_status", "page_title", "page_excerpt", "contact_candidates", "link_evidence", "updated_at"])
    import json
    LinkProspectEvent.objects.create(prospect=prospect, actor=actor, action="placement_check" if placement else "research_check", detail=json.dumps(detail))
    return prospect.check_status


def generate_pitch(prospect, *, sender="Dipesh", actor=None):
    if prospect.status in {LinkProspect.Status.DO_NOT_CONTACT, LinkProspect.Status.DECLINED,
                           LinkProspect.Status.CONTACTED, LinkProspect.Status.REPLIED, LinkProspect.Status.PLACED}:
        raise ValidationError("Drafts can only be generated before outreach and for active prospects.")
    if not prospect.audience.strip() or not prospect.fit_reason.strip():
        raise ValidationError("Record the audience and a specific reason this resource helps them first.")
    target = prospect.target_url or default_target(prospect)
    if not target_is_ours(target):
        raise ValidationError("The pitch must link to a Remote Impact page.")
    greeting = prospect.contact_name.strip() or f"{prospect.name} team"
    if prospect.kind == LinkProspect.Kind.RESEARCH:
        offer = "We publish a dated snapshot of the impact jobs we track, with the methodology and coverage limits explained."
        ask = "If it is useful for your coverage, would you consider citing the snapshot?"
    elif prospect.kind == LinkProspect.Kind.COMMUNITY:
        offer = "We offer a free jobs feed that can help you keep a jobs section current."
        ask = "Would a relevant jobs feed or a link in your community resources be useful?"
    else:
        offer = "Remote Impact helps people find remote roles in climate, global health, AI safety and social impact, with links to the original postings."
        ask = "Would you consider including it among your career resources if it meets your editorial criteria?"
    prospect.target_url = target
    prospect.subject = f"A remote impact careers resource for {prospect.audience}"[:255]
    prospect.pitch = (f"Hi {greeting},\n\nI came across your page: {prospect.url}\n\n"
                      f"{prospect.fit_reason.strip()}\n\n{offer}\n\n{prospect.campaign_url or target}\n\n{ask}\n\n"
                      f"If this is not relevant, let me know and I won't follow up.\n\n{sender}\nRemote Impact")
    prospect.status = LinkProspect.Status.DRAFT
    prospect.reviewed_at = None
    prospect.reviewed_by = None
    prospect.full_clean()
    prospect.save()
    LinkProspectEvent.objects.create(prospect=prospect, actor=actor, action="draft_generated")


@transaction.atomic
def approve(prospect, actor):
    prospect = LinkProspect.objects.select_for_update().get(pk=prospect.pk)
    if prospect.status not in {LinkProspect.Status.DRAFT, LinkProspect.Status.APPROVED}:
        raise ValidationError("Generate or write a draft before reviewing it.")
    if not target_is_ours(prospect.target_url):
        raise ValidationError("Choose a Remote Impact target URL.")
    prospect.status = LinkProspect.Status.APPROVED
    prospect.full_clean()
    prospect.reviewed_at, prospect.reviewed_by = timezone.now(), actor
    prospect.save()
    LinkProspectEvent.objects.create(prospect=prospect, actor=actor, action="reviewed")


@transaction.atomic
def record_contact(prospect, actor):
    from datetime import timedelta
    prospect = LinkProspect.objects.select_for_update().get(pk=prospect.pk)
    if prospect.status != LinkProspect.Status.APPROVED or not prospect.reviewed_at:
        raise ValidationError("Review the current draft before recording outreach.")
    prospect.status = LinkProspect.Status.CONTACTED
    prospect.contacted_at = timezone.now()
    prospect.follow_up_on = prospect.follow_up_on or timezone.localdate() + timedelta(days=7)
    prospect.save()
    LinkProspectEvent.objects.create(prospect=prospect, actor=actor, action="contact_recorded", detail="Recorded by staff; this system did not send a message.")


def import_prospect(row, *, source=""):
    """Idempotent import: never overwrite research, contact history or opt-outs."""
    url = canonical_prospect_url(row.get("url", ""))
    data = {field: row[field] for field in ("name", "sector", "kind", "audience", "fit_reason", "target_url", "priority") if row.get(field)}
    data.setdefault("name", urlsplit(url).hostname)
    data["source"] = (source or row.get("source", "Imported research"))[:500]
    candidate = LinkProspect(url=url, **data)
    candidate.full_clean(validate_unique=False)
    return LinkProspect.objects.get_or_create(url=url, defaults=data)
