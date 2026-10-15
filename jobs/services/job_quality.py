"""Quality scoring and conservative cleanup for active job inventory."""

from __future__ import annotations

from decimal import Decimal

from jobs.services.compensation_parser import extract_annual_salary
from jobs.services.job_enrichment import infer_category_slug
from jobs.services.raw_data_extractor import RawDataExtractor


def normalize_salary(job) -> list[str]:
    """Normalize suspect stored compensation and return changed field names."""
    changed = []
    minimum, maximum = job.salary_min, job.salary_max
    raw = job.raw_data or {}

    if job.source == "idealist":
        period = str(raw.get("salaryPeriod") or "YEAR").upper()
        if period in {'HOUR','DAY','WEEK'}:
            # Keep original rates in raw_data; annual fields cannot assume hours.
            job.salary_min = job.salary_max = None
            changed.extend(['salary_min','salary_max'])
            changed.extend(_mark_salary_rejected(job, f'idealist salaryPeriod={period}; hours unknown'))
            return list(dict.fromkeys(changed))
        multiplier = {"MONTH": 12}.get(period)
        quality = (raw.get("quality_enrichment") or {}).get("salary", {})
        if multiplier and quality.get("method") != "source_period_normalization_v1":
            if minimum is not None:
                job.salary_min = minimum * multiplier
                changed.append("salary_min")
            if maximum is not None:
                job.salary_max = maximum * multiplier
                changed.append("salary_max")
            quality_data = dict(raw.get("quality_enrichment") or {})
            quality_data["salary"] = {
                "method": "source_period_normalization_v1",
                "evidence": f"idealist salaryPeriod={period}",
            }
            raw = dict(raw)
            raw["quality_enrichment"] = quality_data
            job.raw_data = raw
            changed.append("raw_data")

    if job.source == "probablygood" and "₹" in str(raw.get("salary_text") or ""):
        if job.salary_currency != "INR":
            job.salary_currency = "INR"
            changed.append("salary_currency")

    if job.source == "80000hours" and job.salary_max and job.salary_max >= 9_000_000:
        job.salary_max = None
        changed.append("salary_max")
    if job.source == "80000hours" and raw.get("salary"):
        source_salary = RawDataExtractor.extract(job.source, raw)
        low, high = source_salary.get("salary_min"), source_salary.get("salary_max")
        currency = source_salary.get("salary_currency")
        if low is not None and high is not None and low <= high:
            if job.salary_min != low:
                job.salary_min = low
                changed.append("salary_min")
            if job.salary_max != high:
                job.salary_max = high
                changed.append("salary_max")
            if currency and job.salary_currency != currency:
                job.salary_currency = currency
                changed.append("salary_currency")

    minimum, maximum = job.salary_min, job.salary_max
    suspect_low = any(value is not None and 0 <= value < 10_000 for value in (minimum, maximum))
    if suspect_low:
        extracted = extract_annual_salary(" ".join((job.requirements or "", job.description or "")))
        if extracted:
            job.salary_min = Decimal(str(extracted["salary_min"]))
            job.salary_max = Decimal(str(extracted["salary_max"]))
            job.salary_currency = extracted["salary_currency"]
            changed.extend(["salary_min", "salary_max", "salary_currency"])
        else:
            job.salary_min = None
            job.salary_max = None
            changed.extend(["salary_min", "salary_max"])
            changed.extend(_mark_salary_rejected(job, "ambiguous or non-annual compensation"))

    if job.salary_min is not None and job.salary_max is not None and job.salary_min > job.salary_max:
        job.salary_min = None
        job.salary_max = None
        changed.extend(["salary_min", "salary_max"])
        changed.extend(_mark_salary_rejected(job, "salary minimum exceeds maximum"))
    if any(
        value is not None
        and (
            value > 10_000_000
            or (job.salary_currency == "USD" and value >= 1_000_000)
        )
        for value in (job.salary_min, job.salary_max)
    ):
        job.salary_min = None
        job.salary_max = None
        changed.extend(["salary_min", "salary_max"])
        changed.extend(_mark_salary_rejected(job, "compensation exceeds sanity ceiling"))
    return list(dict.fromkeys(changed))


def _mark_salary_rejected(job, evidence: str) -> list[str]:
    raw = dict(job.raw_data or {})
    quality = dict(raw.get("quality_enrichment") or {})
    quality["salary"] = {
        "method": "rejected_non_annual_v1",
        "evidence": evidence,
    }
    raw["quality_enrichment"] = quality
    job.raw_data = raw
    return ["raw_data"]


def score_job(job) -> tuple[int, list[str]]:
    issues = []
    score = 0
    if len((job.description or "").strip()) >= 200:
        score += 25
    else:
        issues.append("thin_description")
    if job.application_url or job.application_email:
        score += 15
    else:
        issues.append("missing_application_destination")
    if job.country:
        score += 10
    else:
        issues.append("missing_country")
    if job.skills:
        score += 15
    else:
        issues.append("missing_skills")
    if job.category_id and job.category.slug != "other":
        score += 10
    else:
        issues.append("generic_category")
    if job.experience_level:
        score += 8
    else:
        issues.append("missing_experience")
    if job.education_level:
        score += 5
    else:
        issues.append("missing_education")
    if job.salary_min is not None or job.salary_max is not None:
        score += 12
    else:
        issues.append("missing_salary")
    # Optional metadata must not compensate for a missing usable description.
    ceiling = 54 if "thin_description" in issues else 100
    return min(score, ceiling), issues


def refresh_job_quality(job) -> bool:
    """Normalize and score a job before it is exposed to public workflows."""
    changed = bool(normalize_salary(job))
    score, issues = score_job(job)
    if job.quality_score != score:
        job.quality_score = score
        changed = True
    if job.quality_issues != issues:
        job.quality_issues = issues
        changed = True
    return changed


def better_category_slug(job) -> str | None:
    if not job.category_id or job.category.slug == "other":
        slug = infer_category_slug(job.title, job.description, job.requirements)
        return slug if slug != "other" else None
    return None


def safe_application_destination(value: str) -> str:
    """Allow only ordinary HTTP(S) links; this does not fetch or verify them."""
    from urllib.parse import urlsplit

    try:
        if not value or any(ord(char) < 32 for char in value):
            return ""
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return ""


def schema_applicant_countries(job) -> list[str]:
    """Return only explicit, unambiguous source eligibility countries.

    Contradictory or region/state-specific constraints are omitted rather than
    translated into a falsely complete Country requirement.
    """
    import re
    from django_countries import countries
    from jobs.services.scout_constraints import REGIONS, places, plain_job_field, state_names

    body = " ".join(plain_job_field(job, field) for field in ("description", "requirements"))
    body = re.sub(r"\bU\.S\.(?:A\.)?", "United States", body, flags=re.I)
    positive = []
    for clause in re.split(r"[.!?;\n]", body):
        if not re.search(
            r"remote\s+(?:within|from|in)|(?:must|need to|only)\s+(?:be\s+)?(?:based|reside|located)|"
            r"(?:candidates|applicants)\s+(?:must\s+)?(?:be\s+)?(?:based|reside|located)",
            clause, re.I,
        ):
            continue
        if re.search(r"\b(?:not|except|excluding|unable|cannot)\b|n't", clause, re.I):
            return []
        found = places(clause)
        if state_names(clause) or any(term in REGIONS for term in found):
            return []
        codes = set().union(*found.values()) if found else set()
        if codes:
            positive.append(codes)
    if not positive or any(codes != positive[0] for codes in positive[1:]):
        return []
    selected = positive[0]
    for field in ("country", "location"):
        metadata = places(plain_job_field(job, field), codes=True)
        if any(term in REGIONS for term in metadata) or state_names(plain_job_field(job, field)):
            return []
        offered = set().union(*metadata.values()) if metadata else set()
        if offered and offered != selected:
            return []
    # Exclusions elsewhere in the source may narrow the positive scope.
    if re.search(r"\b(?:not eligible|cannot hire|unable to hire|excluding|except)\b", body, re.I):
        return []
    return [str(countries.name(code)) for code in sorted(selected)]


EU_COUNTRY_CODES = "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split()
LABEL_COUNTRY_CODES = {"US": ["US"], "USA": ["US"], "UK": ["GB"], "EU": EU_COUNTRY_CODES}
RESIDENCY_CLAUSE = (
    r"remote\s+(?:within|from|in)|(?:must|need to|only)\s+(?:be\s+)?(?:based|reside|located)|"
    r"(?:candidates|applicants)\s+(?:must\s+)?(?:be\s+)?(?:based|reside|located)|work\s+authori[sz]ation|"
    r"eligible\s+to\s+work|right\s+to\s+work"
)


def label_applicant_countries(job) -> list[str]:
    """Countries from the visible "Remote (US)" style label, when the listing says nothing else about where
    applicants must live. Any residency wording in the text leaves the decision to the strict source rules."""
    import re
    from django_countries import countries
    from jobs.services.scout_constraints import plain_job_field

    match = re.fullmatch(r"\s*remote\s*\(([^)]+)\)\s*", job.location or "", re.I)
    if not match:
        return []
    body = " ".join(plain_job_field(job, field) for field in ("description", "requirements"))
    if re.search(RESIDENCY_CLAUSE, body, re.I):
        return []
    label = match.group(1).strip()
    codes = LABEL_COUNTRY_CODES.get(label.upper())
    if codes is None:
        code = countries.by_name(label)
        codes = [code] if code else []
    return [str(countries.name(code)) for code in codes]


def _schema_description(job) -> str:
    """Listing text with its paragraph and list breaks kept (strip_tags alone runs sentences together)."""
    import re
    from django.utils.html import strip_tags

    text = job.description or job.requirements or job.impact or job.title
    text = re.sub(r"(?i)<\s*(?:br\s*/?|/p|/li|/h[1-6]|/div|/ul|/ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = strip_tags(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[:5000]


def job_posting_schema_json(job, site_url: str) -> str:
    """Serialize source facts without invented expiry, employer identity or geography."""
    import json
    import re
    from django.utils.html import strip_tags

    data = {
        "@context": "https://schema.org", "@type": "JobPosting",
        "title": job.title, "description": _schema_description(job),
        "identifier": {"@type": "PropertyValue", "name": job.organization.name, "value": str(job.pk)},
        "url": site_url.rstrip("/") + job.get_absolute_url(),
        "datePosted": job.posted_at.isoformat(),
        "hiringOrganization": {"@type": "Organization", "name": job.organization.name},
        "employmentType": {"full-time": "FULL_TIME", "part-time": "PART_TIME", "contract": "CONTRACTOR", "freelance": "CONTRACTOR"}.get(job.job_type, "OTHER"),
        "directApply": job.source == job.Source.MANUAL,
    }
    if job.expires_at:
        data["validThrough"] = job.expires_at.isoformat()
    website = safe_application_destination(job.organization.website)
    if website:
        data["hiringOrganization"]["sameAs"] = website
    logo = job.organization.logo_src
    if logo:
        data["hiringOrganization"]["logo"] = site_url.rstrip("/") + logo if logo.startswith("/") else logo
    # Eligibility must be grounded in positive residency/remote-work source
    # evidence. A normalized country field alone can be a lossy import label.
    remote = bool(re.search(r"\bremote\b", job.location or "", re.I))
    if remote:
        data["jobLocationType"] = "TELECOMMUTE"
        eligible_countries = schema_applicant_countries(job) or label_applicant_countries(job)
        if eligible_countries:
            requirements = [{"@type": "Country", "name": name} for name in eligible_countries]
            data["applicantLocationRequirements"] = requirements[0] if len(requirements) == 1 else requirements
    elif job.location:
        data["jobLocation"] = {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": job.location}}
    raw = job.raw_data or {}
    salary_meta = (raw.get("quality_enrichment") or {}).get("salary") or {}
    annual_evidence = (
        job.source == job.Source.MANUAL
        or str(raw.get("salaryPeriod") or "").upper() == "YEAR"
        or salary_meta.get("method") == "source_period_normalization_v1"
    )
    # Legacy enrichment can retain an annualized estimate for hourly work.
    # The explicit source unit takes precedence over that cached metadata.
    source_period = str(raw.get("salaryPeriod") or raw.get("salary_period") or "").lower()
    if source_period in {"hour", "hourly", "hr", "day", "daily", "week", "weekly"}:
        annual_evidence = False
    salary_values = [value for value in (job.salary_min, job.salary_max) if value is not None]
    currency = str(job.salary_currency or "")
    if annual_evidence and salary_values and all(0 < value <= 10_000_000 for value in salary_values) and re.fullmatch(r"[A-Z]{3}", currency) and not str(salary_meta.get("method", "")).startswith("rejected"):
        if job.salary_min is None or job.salary_max is None or job.salary_min <= job.salary_max:
            value = {"@type": "QuantitativeValue", "unitText": "YEAR"}
            if job.salary_min is not None:
                value["minValue"] = float(job.salary_min)
            if job.salary_max is not None:
                value["maxValue"] = float(job.salary_max)
            data["baseSalary"] = {"@type": "MonetaryAmount", "currency": currency, "value": value}
    # JSON serialization is necessary: escapejs emits JavaScript escapes (\\u0027
    # is valid, \\x is not JSON), and source text may contain a script terminator.
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def google_job_posting_json(job, site_url: str) -> str:
    """Keep factual JobPosting markup even when optional source facts are unknown.

    Missing remote eligibility may still prevent Google's job enhancement. Do
    not hide the entire posting or invent a country to satisfy that validator.
    Availability is checked by the page view before calling this serializer.
    """
    return job_posting_schema_json(job, site_url)
