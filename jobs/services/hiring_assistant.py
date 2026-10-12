"""Hiring assistant for paid job posts.

Everything here is advisory and grounded: a must-have counts only when its quote really appears in
the posting, and an applicant "meets" one only when a quote from their own words backs it. Nothing
rejects, ranks out or contacts anyone on its own; the employer reads, decides and sends.
"""
import json
import logging
import re

from django.utils import timezone

from jobs.services.scout_constraints import plain_job_field

logger = logging.getLogger(__name__)

MAX_MUST_HAVES = 6
STATUSES = ("met", "partly", "not_shown")


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def verified(quote, body, *, min_len=6, max_len=240):
    return bool(quote) and min_len <= len(quote) <= max_len and _norm(quote) in _norm(body)


def _json(text):
    text = text or ""
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1]) if start >= 0 and end > start else {}


def _client():
    from jobs.services.ai import AIClient

    return AIClient()


# ---------------------------------------------------------------------------
# The role's must-haves
# ---------------------------------------------------------------------------

MUST_HAVE_SYSTEM = (
    "You read a job posting and list what it says a candidate must have: skills, experience, qualifications, "
    "languages, work authorisation or location. Use only what the posting states. Skip nice-to-haves and "
    "anything about the employer. At most 6 items, most important first. Each item needs a short label "
    "(3-8 words) and a verbatim quote (8-200 characters) copied exactly from the posting. "
    'Return JSON only: {"must_haves":[{"label":..,"quote":..}]}'
)


def posting_text(job):
    return " ".join(plain_job_field(job, field) for field in ("description", "requirements"))[:8000]


def extract_must_haves(job, client=None):
    body = posting_text(job)
    if len(body) < 200:
        return []
    client = client or _client()
    prompt = json.dumps({"title": job.title, "location": job.location, "posting": body}, ensure_ascii=False)
    for _ in range(2):
        try:
            raw = _json(client.generate(prompt, max_tokens=700, temperature=0, timeout=45, max_retries=1,
                                        system_message=MUST_HAVE_SYSTEM))
            items = []
            for item in raw.get("must_haves") or []:
                label, quote = str(item.get("label", "")).strip()[:80], str(item.get("quote", "")).strip()
                if label and verified(quote, body, min_len=8, max_len=220):
                    items.append({"label": label, "quote": quote})
            return items[:MAX_MUST_HAVES]
        except Exception:  # noqa: BLE001 - try once more, then give up quietly
            continue
    return []


def ensure_must_haves(job, client=None):
    if job.must_haves:
        return job.must_haves
    items = extract_must_haves(job, client)
    if items:
        type(job).objects.filter(pk=job.pk).update(must_haves=items)
        job.must_haves = items
    return items


# ---------------------------------------------------------------------------
# Screening an application
# ---------------------------------------------------------------------------

SCREEN_SYSTEM = (
    "You help an employer read one job application against the role's must-haves. For each must-have, "
    "decide from the applicant's material only: \"met\" (clearly shown), \"partly\" (related but not the same) "
    "or \"not_shown\" (not mentioned). Never guess or infer beyond the text. For met or partly, give a "
    "verbatim quote (6-200 characters) copied exactly from the applicant's material. Then write a two-sentence "
    "neutral summary of what the applicant offers for this role, using only facts in their material. "
    "Do not judge personality, age, gender, nationality or anything not job-related. "
    'Return JSON only: {"checks":[{"index":0,"status":..,"quote":..}],"summary":..}'
)


def applicant_material(application):
    """Everything the applicant wrote for this role, plus their Remote Impact profile."""
    parts = []
    for label, value in (("Why I'm a fit", application.why_great_fit), ("Cover letter", application.cover_letter),
                         ("Location", application.current_location),
                         ("Experience", application.get_years_experience_display() if application.years_experience else ""),
                         ("Salary expectation", application.salary_expectation)):
        if value:
            parts.append(f"{label}: {value}")
    for answer in application.custom_question_answers or []:
        if answer.get("answer"):
            parts.append(f"{answer.get('question', 'Question')}: {answer['answer']}")
    profile = getattr(application.applicant, "seeker_profile", None)
    if profile:
        for label, value in (("Headline", profile.headline), ("About", profile.bio),
                             ("Impact", profile.impact_statement),
                             ("Skills", ", ".join(profile.skills or []))):
            if value:
                parts.append(f"{label}: {value}")
    return "\n".join(parts)[:9000]


def eligibility_notes(application, job):
    """Plain facts the employer should see side by side; no verdicts."""
    notes = []
    if application.current_location:
        notes.append({"label": "Location", "applicant": application.current_location, "role": job.location or "Remote"})
    if application.salary_expectation:
        role_pay = ""
        if job.salary_min or job.salary_max:
            role_pay = f"{job.salary_currency or ''} {int(job.salary_min or 0):,}–{int(job.salary_max or 0):,}".strip()
        notes.append({"label": "Pay", "applicant": application.salary_expectation, "role": role_pay or "Not stated"})
    return notes


def assess(job, material, client=None):
    """Compare written material with the role's must-haves; only quoted evidence counts."""
    must_haves = ensure_must_haves(job, client)
    result = {"must_haves": [], "met": 0, "partly": 0, "total": len(must_haves), "summary": "",
              "screened_at": timezone.now().isoformat()}
    if not must_haves or len(material) < 40:
        result["thin"] = len(material) < 40
        result["must_haves"] = [{"label": m["label"], "status": "not_shown", "quote": ""} for m in must_haves]
        return result
    client = client or _client()
    prompt = json.dumps({
        "role": job.title,
        "must_haves": [{"index": i, "label": m["label"], "posting_says": m["quote"]} for i, m in enumerate(must_haves)],
        "applicant_material": material,
    }, ensure_ascii=False)
    raw = {}
    for _ in range(2):
        try:
            raw = _json(client.generate(prompt, max_tokens=900, temperature=0, timeout=60, max_retries=1,
                                        system_message=SCREEN_SYSTEM))
            break
        except Exception:  # noqa: BLE001
            continue
    by_index = {}
    for check in raw.get("checks") or []:
        try:
            by_index[int(check.get("index"))] = check
        except (TypeError, ValueError):
            continue
    for i, must in enumerate(must_haves):
        check = by_index.get(i) or {}
        status, quote = str(check.get("status", "not_shown")), str(check.get("quote", "")).strip()
        # An unbacked claim is treated as not shown: the employer only sees what the person wrote.
        if status not in STATUSES or (status != "not_shown" and not verified(quote, material)):
            status, quote = "not_shown", ""
        result["must_haves"].append({"label": must["label"], "status": status, "quote": quote if status != "not_shown" else ""})
    result["met"] = sum(1 for m in result["must_haves"] if m["status"] == "met")
    result["partly"] = sum(1 for m in result["must_haves"] if m["status"] == "partly")
    result["summary"] = str(raw.get("summary", "")).strip()[:500]
    return result


def screen_application(application, client=None):
    result = assess(application.job, applicant_material(application), client)
    result["notes"] = eligibility_notes(application, application.job)
    return result


def profile_material(profile):
    parts = []
    for label, value in (("Headline", profile.headline), ("About", profile.bio), ("Impact", profile.impact_statement),
                         ("Skills", ", ".join(profile.skills or [])),
                         ("Experience level", profile.experience_level or "")):
        if value:
            parts.append(f"{label}: {value}")
    return "\n".join(parts)[:6000]


def screen_and_save(application, client=None):
    try:
        result = screen_application(application, client)
    except Exception:  # noqa: BLE001 - screening must never break an application
        logger.exception("Screening failed for application %s", application.pk)
        return None
    type(application).objects.filter(pk=application.pk).update(screening=result)
    application.screening = result
    return result


# ---------------------------------------------------------------------------
# Sourcing: a drafted invitation for a suggested candidate
# ---------------------------------------------------------------------------

INVITE_SYSTEM = (
    "Write a short, warm invitation (60-110 words) from an employer to a candidate, asking them to apply for "
    "a role. Mention only the evidence you are given, in your own words; do not invent facts about the "
    "candidate or the employer. The email already links to the role, so don't say where to find it. No flattery, "
    "no promises about the outcome. Plain text, no subject line, no greeting name, no signature."
)


def draft_invite(job, evidence, client=None):
    """evidence: list of {"label", "quote"} from the candidate's own profile."""
    points = [f"{e['label']} (they wrote: \"{e['quote']}\")" for e in evidence][:4]
    fallback = (f"Hi,\n\nWe're hiring a {job.title} at {job.organization.name}. Your profile stood out"
                f"{' for ' + ', '.join(e['label'].lower() for e in evidence[:3]) if evidence else ''}, and we'd be glad "
                f"if you applied.\n\nThanks,")
    try:
        client = client or _client()
        prompt = json.dumps({"role": job.title, "organization": job.organization.name, "evidence": points},
                            ensure_ascii=False)
        text = client.generate(prompt, max_tokens=250, temperature=0.4, timeout=30, max_retries=1,
                               system_message=INVITE_SYSTEM).strip()
        return text if 40 <= len(text) <= 1200 else fallback
    except Exception:  # noqa: BLE001
        return fallback


def sourcing_evidence(job, profile, client=None):
    """Must-haves the candidate's own profile shows, with their words; [] when none."""
    result = assess(job, profile_material(profile), client)
    return [{"label": m["label"], "quote": m["quote"]} for m in result["must_haves"] if m["status"] == "met"]


# ---------------------------------------------------------------------------
# Listing coach: concrete fixes that help a listing perform
# ---------------------------------------------------------------------------

def listing_checks(job):
    tips = []
    if not (job.salary_min or job.salary_max):
        tips.append("Add a pay range. Many candidates skip roles that don't show pay, and search filters by salary.")
    location = (job.location or "").lower()
    if location in ("", "remote", "anywhere", "worldwide") and not job.country:
        tips.append("Say which countries or time zones can apply, so people outside them don't spend time applying.")
    if len(posting_text(job)) < 1200:
        tips.append("The description is short. Add what the person will do in their first months and what success looks like.")
    if not job.skills:
        tips.append("Add the key skills, so we can match and suggest the right candidates.")
    if not job.experience_level:
        tips.append("Set the experience level, so the role reaches people at the right stage.")
    return tips
