"""Read an uploaded CV into text and grounded facts (roles, skills, education, languages).

Every fact carries a verbatim quote from the CV, checked in code, so matching and the hiring assistant
only ever rely on what the person actually wrote. Supports PDF and DOCX (old .doc is not read).
"""
import io
import json
import logging
import re

logger = logging.getLogger(__name__)

MAX_PAGES = 12
MAX_CHARS = 30000


def extract_text(file_obj, name=""):
    """Plain text from a PDF or DOCX file object; "" when unreadable or unsupported."""
    name = (name or getattr(file_obj, "name", "") or "").lower()
    try:
        data = file_obj.read()
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        if name.endswith(".pdf") or data[:5] == b"%PDF-":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:MAX_PAGES])
        elif name.endswith(".docx") or data[:2] == b"PK":
            import docx

            document = docx.Document(io.BytesIO(data))
            parts = [p.text for p in document.paragraphs]
            for table in document.tables[:20]:
                for row in table.rows:
                    parts.append(" | ".join(cell.text for cell in row.cells))
            text = "\n".join(parts)
        else:
            return ""
    except Exception:  # noqa: BLE001 - a broken file simply yields no text
        logger.warning("Could not read CV %s", name or "(unnamed)")
        return ""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_CHARS]


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _grounded(quote, body):
    return bool(quote) and 3 <= len(quote) <= 240 and _norm(quote) in _norm(body)


SYSTEM = (
    "You read a CV and list facts stated in it. Use only what the CV says; never guess. For each item give a "
    "verbatim quote (3-200 characters) copied exactly from the CV. Roles: job title, organization, years "
    "(as written, e.g. '2019-2023'). Skills: concrete professional skills or tools (max 25). Education: degree "
    "and institution. Languages: spoken languages with level if stated. Also give the total years of work "
    "experience only if the dates make it clear, else null. "
    'Return JSON only: {"roles":[{"title":..,"organization":..,"years":..,"quote":..}],'
    '"skills":[{"name":..,"quote":..}],"education":[{"degree":..,"institution":..,"quote":..}],'
    '"languages":[{"language":..,"level":..,"quote":..}],"total_years":null}'
)


def read_facts(text, client=None):
    """Grounded facts from CV text; items whose quote isn't in the CV are dropped."""
    if len(text or "") < 200:
        return {}
    if client is None:
        from jobs.services.ai import AIClient
        client = AIClient()
    body = text[:14000]
    raw = {}
    for _ in range(2):
        try:
            out = client.generate(json.dumps({"cv": body}, ensure_ascii=False), max_tokens=1800, temperature=0,
                                  timeout=60, max_retries=1, system_message=SYSTEM)
            raw = json.loads(out[out.find("{"): out.rfind("}") + 1])
            break
        except Exception:  # noqa: BLE001
            continue
    facts = {}
    for key, fields in (("roles", ("title", "organization", "years")), ("skills", ("name",)),
                        ("education", ("degree", "institution")), ("languages", ("language", "level"))):
        kept = []
        for item in raw.get(key) or []:
            if isinstance(item, dict) and _grounded(str(item.get("quote", "")), body):
                kept.append({f: str(item.get(f) or "")[:120] for f in fields} | {"quote": str(item["quote"])[:240]})
        facts[key] = kept[:25]
    years = raw.get("total_years")
    facts["total_years"] = years if isinstance(years, (int, float)) and 0 <= years <= 60 else None
    return facts


def facts_summary(facts):
    """Short plain text of the facts, for embeddings and the hiring assistant."""
    if not facts:
        return ""
    lines = []
    for role in facts.get("roles", [])[:8]:
        lines.append(" ".join(v for v in (role["title"], "at " + role["organization"] if role["organization"] else "", role["years"]) if v))
    if facts.get("skills"):
        lines.append("Skills: " + ", ".join(s["name"] for s in facts["skills"]))
    for edu in facts.get("education", [])[:3]:
        lines.append(" ".join(v for v in (edu["degree"], edu["institution"]) if v))
    if facts.get("languages"):
        lines.append("Languages: " + ", ".join(" ".join(v for v in (l["language"], l["level"]) if v) for l in facts["languages"]))
    return "\n".join(lines)


def read_profile_cv(profile, client=None):
    """Extract and store a profile's CV text and facts. Returns True when something was stored."""
    from django.utils import timezone

    if not profile.resume:
        return False
    try:
        with profile.resume.open("rb") as handle:
            text = extract_text(handle, profile.resume.name)
    except Exception:  # noqa: BLE001 - missing file on disk
        logger.warning("CV file missing for profile %s", profile.pk)
        return False
    facts = read_facts(text, client) if text else {}
    type(profile).objects.filter(pk=profile.pk).update(resume_text=text, cv_facts=facts, cv_read_at=timezone.now())
    profile.resume_text, profile.cv_facts = text, facts
    return bool(text)
