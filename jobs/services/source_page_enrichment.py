"""Conservative enrichment from the original public job posting page.

This is deliberately a bounded fetch -> extract -> verify workflow, not an
open-ended web agent.  The source page must look like the specific opening,
and experience, education, and compensation are only saved when DeepSeek
returns a quote that exists on that page.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from jobs.services.safe_fetch import fetch_public_html, resolve_public_url, UnsafeURL
from bs4 import BeautifulSoup, Comment
from django.utils import timezone

from jobs.models import Category, Job
from jobs.services.job_quality import normalize_salary, score_job
from jobs.services.llm_parser import _evidence_is_present


FETCH_HEADERS = {
    "User-Agent": "RemoteImpactDataQuality/1.0 (+https://remoteimpact.org)",
    "Accept": "text/html,application/xhtml+xml",
}
MAX_PAGE_CHARS = 30_000
SOURCE_ENRICHMENT_KEY = "source_page_enrichment"
SOURCE_ENRICHMENT_VERSION = "source_page_deepseek_v1"
ATTEMPT_COOLDOWN_DAYS = 30
RETRY_COOLDOWN_DAYS = 1
MAX_REDIRECTS = 3
RETRYABLE_STATUSES = {
    "request_failed", "llm_failed", "http_302", "http_429", "http_500", "http_502", "http_503", "http_504",
}


@dataclass
class PageFetch:
    status: str
    text: str = ""


def _public_http_url(url: str) -> bool:
    """Compatibility predicate; actual fetch also pins the validated address."""
    try:
        resolve_public_url(url)
        return True
    except (UnsafeURL, UnicodeError):
        return False


def fetch_job_page(url: str, *, title: str = "") -> PageFetch:
    """Fetch a bounded public HTML page with redirect and DNS protections."""
    response = fetch_public_html(url, headers=FETCH_HEADERS, max_redirects=MAX_REDIRECTS)
    if response.status != "ok":
        return PageFetch(response.status)

    soup = BeautifulSoup(response.text, "html.parser")
    postings = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        pending = [data]
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, dict):
                types = item.get("@type", [])
                if "JobPosting" in (types if isinstance(types, list) else [types]):
                    postings.append(item)
                if isinstance(item.get("@graph"), list):
                    pending.extend(item["@graph"])
    if postings:
        matches = [item for item in postings if not title or
                   re.sub(r"\W+", " ", str(item.get("title", ""))).strip().casefold() ==
                   re.sub(r"\W+", " ", title).strip().casefold()]
        if len(matches) == 1:
            posting = matches[0]
            description = BeautifulSoup(str(posting.get("description") or ""), "html.parser").get_text(" ", strip=True)
            if len(description) >= 200:
                # Include structured fields in the evidence text, not scripts or
                # unrelated openings from a board page.
                text = str(posting.get("title") or "") + " " + description
                for field in ("qualifications", "educationRequirements", "experienceRequirements", "baseSalary"):
                    if posting.get(field):
                        text += " " + field + ": " + json.dumps(posting[field], ensure_ascii=True)
                return PageFetch("ok", text[:MAX_PAGE_CHARS])
        if len(postings) > 1:
            return PageFetch("ambiguous_job_page")
    for tag in soup.find_all(("script", "style", "nav", "footer", "header", "iframe", "noscript")):
        tag.decompose()
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    main = soup.find("main") or soup.find("article") or soup.find(
        class_=re.compile(r"job|description|posting|content", re.IGNORECASE)
    )
    text = (main or soup.body or soup).get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 250:
        return PageFetch("thin_page")
    return PageFetch("ok", text[:MAX_PAGE_CHARS])


def page_matches_title(title: str, page_text: str) -> bool:
    """Require the fetched page to contain enough of the opening's title."""
    title_tokens = [
        token for token in re.findall(r"[a-z0-9+#.]{3,}", title.lower())
        if token not in {"the", "and", "for", "with", "remote"}
    ]
    if not title_tokens:
        return False
    page_tokens = set(re.findall(r"[a-z0-9+#.]{3,}", page_text.lower()))
    matched = sum(token in page_tokens for token in title_tokens)
    required = 1 if len(title_tokens) <= 2 else max(2, (len(title_tokens) + 1) // 2)
    return matched >= required


def eligible_for_source_enrichment(job: Job, *, force: bool = False) -> bool:
    if not job.application_url or job.source in {Job.Source.MANUAL, Job.Source.IDEALIST}:
        return False
    if force:
        return True
    existing = (job.raw_data or {}).get(SOURCE_ENRICHMENT_KEY) or {}
    attempted_at = existing.get("attempted_at")
    if not attempted_at:
        return True
    try:
        attempted = datetime.fromisoformat(attempted_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if timezone.is_naive(attempted):
        attempted = timezone.make_aware(attempted, dt_timezone.utc)
    days = RETRY_COOLDOWN_DAYS if existing.get("status") in RETRYABLE_STATUSES else ATTEMPT_COOLDOWN_DAYS
    return attempted <= timezone.now() - timedelta(days=days)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return decimal_value if decimal_value.is_finite() and decimal_value >= 0 else None


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == []


def _record_attempt(job: Job, *, status: str, page_text: str = "", fields: list[str] | None = None,
                    evidence: dict[str, str] | None = None) -> None:
    raw_data = dict(job.raw_data or {})
    raw_data[SOURCE_ENRICHMENT_KEY] = {
        "version": SOURCE_ENRICHMENT_VERSION,
        "status": status,
        "attempted_at": timezone.now().isoformat(),
        "source_url": job.application_url,
        "was_active": job.is_active,
        "content_sha256": hashlib.sha256(page_text.encode("utf-8")).hexdigest() if page_text else "",
        "updated_fields": fields or [],
        "evidence": evidence or {},
    }
    job.raw_data = raw_data


def apply_source_page_enrichment(job: Job, parsed: dict[str, Any], page_text: str) -> list[str]:
    """Merge only supported, currently missing values and return changed fields."""
    changed: list[str] = []
    evidence: dict[str, str] = {}
    categories = Category.objects.in_bulk(field_name="slug")

    if len((job.description or "").strip()) < 200 and len(page_text) >= 250:
        job.description = page_text
        evidence["description"] = "Title-confirmed source page"
        changed.append("description")

    if (not job.category_id or job.category.slug == "other"):
        category = categories.get(str(parsed.get("impact_area") or ""))
        if category and category.slug != "other":
            job.category = category
            changed.append("category")
    if _missing(job.country) and parsed.get("country"):
        job.country = str(parsed["country"])[:100]
        changed.append("country")
    if _missing(job.job_type) and parsed.get("job_type") in {"full-time", "part-time", "contract", "freelance"}:
        job.job_type = parsed["job_type"]
        changed.append("job_type")
    if _missing(job.experience_level) and parsed.get("experience_level") in dict(Job.EXPERIENCE_LEVEL_CHOICES):
        quote = parsed.get("experience_evidence")
        if _evidence_is_present(quote, page_text):
            job.experience_level = parsed["experience_level"]
            evidence["experience_level"] = quote.strip()[:240]
            changed.append("experience_level")
    if _missing(job.education_level) and parsed.get("education_level") in dict(Job.EDUCATION_LEVEL_CHOICES):
        quote = parsed.get("education_evidence")
        if _evidence_is_present(quote, page_text):
            job.education_level = parsed["education_level"]
            evidence["education_level"] = quote.strip()[:240]
            changed.append("education_level")
    if _missing(job.skills) and isinstance(parsed.get("skills"), list):
        skills = [str(skill) for skill in parsed["skills"] if isinstance(skill, str)]
        if skills:
            job.skills = skills
            changed.append("skills")

    salary_quote = parsed.get("salary_evidence")
    if job.salary_min is None and job.salary_max is None and _evidence_is_present(salary_quote, page_text):
        minimum, maximum = _decimal(parsed.get("salary_min")), _decimal(parsed.get("salary_max"))
        if minimum is not None or maximum is not None:
            job.salary_min = minimum
            job.salary_max = maximum
            currency = str(parsed.get("salary_currency") or "").upper()
            if re.fullmatch(r"[A-Z]{3}", currency):
                job.salary_currency = currency
            evidence["salary"] = salary_quote.strip()[:240]
            changed.extend(["salary_min", "salary_max", "salary_currency"])

    fields = normalize_salary(job)
    changed.extend(field for field in fields if field not in changed)
    score, issues = score_job(job)
    if job.quality_score != score:
        job.quality_score = score
        changed.append("quality_score")
    if job.quality_issues != issues:
        job.quality_issues = issues
        changed.append("quality_issues")
    _record_attempt(job, status="enriched" if changed else "no_new_supported_fields", page_text=page_text,
                    fields=changed, evidence=evidence)
    changed.append("raw_data")
    return list(dict.fromkeys(changed))
