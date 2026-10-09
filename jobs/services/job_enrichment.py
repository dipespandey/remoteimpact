"""Conservative, deterministic enrichment for imported jobs."""

from __future__ import annotations

import re
from typing import Any

from jobs.services.education_classifier import EducationClassifier
from jobs.services.experience_classifier import ExperienceClassifier
from jobs.services.compensation_parser import extract_annual_salary
from jobs.services.location_normalizer import STANDARD_LOCATIONS, normalize_location
from jobs.services.matching_service import MatchingService
from jobs.services.raw_data_extractor import RawDataExtractor


REMOTE_COUNTRIES = {
    "Remote": "Worldwide",
    "Remote (US)": "USA",
    "Remote (UK)": "UK",
    "Remote (EU)": "Europe",
    "Remote (Canada)": "Canada",
    "Remote (India)": "India",
    "Remote (Australia)": "Australia",
    "Remote (Germany)": "Germany",
    "Remote (APAC)": "Asia Pacific",
    "Remote (LATAM)": "Latin America",
    "Remote (EMEA)": "EMEA",
}

COUNTRY_VALUES = {
    value
    for value in STANDARD_LOCATIONS.values()
    if not value.startswith("Remote")
} | {"Worldwide", "Europe", "Asia Pacific", "Latin America", "EMEA"}

PLACEHOLDER_VALUES = frozenset({
    "",
    "null",
    "none",
    "n/a",
    "not available",
    "unknown",
})


# Cause areas are checked before functional categories. Patterns are intentionally
# specific because a wrong category is more harmful than an "Other" category.
CAUSE_CATEGORY_PATTERNS = (
    ("ai-safety", (r"\bai safety\b", r"\bai alignment\b", r"\bresponsible ai\b", r"\bai governance\b")),
    ("biosecurity", (r"\bbiosecurity\b", r"\bbiodefense\b", r"\bpandemic preparedness\b", r"\bbiological risk\b")),
    ("animal-welfare", (r"\banimal welfare\b", r"\banimal rights\b", r"\bfarmed animals?\b", r"\bfactory farming\b")),
    ("nuclear-security", (r"\bnuclear security\b", r"\bnuclear weapons?\b", r"\bnonproliferation\b", r"\barms control\b")),
    ("humanitarian", (r"\bhumanitarian\b", r"\bdisaster relief\b", r"\bemergency response\b", r"\brefugee response\b")),
    ("human-rights", (r"\bhuman rights\b", r"\bcivil liberties\b", r"\bsocial justice\b", r"\baccess to justice\b")),
    ("gender-equality-social-inclusion", (r"\bgender equality\b", r"\bsocial inclusion\b", r"\blgbtq\b", r"\bdisability inclusion\b")),
    ("poverty-development", (r"\beconomic development\b", r"\bglobal development\b", r"\bfinancial inclusion\b", r"\bpoverty alleviation\b", r"\bcash transfers?\b")),
    ("global-health", (r"\bglobal health\b", r"\bpublic health\b", r"\bhealthcare\b", r"\bhealth care\b", r"\bclinical research\b", r"\binfectious diseases?\b")),
    ("climate-environment", (r"\bclimate\b", r"\bdecarboni[sz]", r"\bcarbon emissions?\b", r"\brenewable energy\b", r"\bsustainab", r"\bbiodiversity\b", r"\bconservation\b")),
    ("effective-altruism", (r"\beffective altruism\b", r"\bglobal priorities\b", r"\bcause prioritization\b")),
)

TITLE_CATEGORY_PATTERNS = (
    ("technology", (r"\bsoftware\b", r"\bengineer(?:ing)?\b", r"\bdeveloper\b", r"\bdata scientist\b", r"\bdata analyst\b", r"\bmachine learning\b", r"\bdevops\b", r"\bproduct manager\b")),
    ("communications", (r"\bcommunications?\b", r"\bmarketing\b", r"\bcontent\b", r"\bsocial media\b", r"\bjournalist\b", r"\bwriter\b", r"\beditor\b", r"\bpublic relations\b")),
    ("policy-advocacy", (r"\bpolicy\b", r"\badvocacy\b", r"\bgovernment affairs\b", r"\bpublic affairs\b", r"\bcampaign(?:er|ing)?\b")),
    ("education", (r"\bteacher\b", r"\beducator\b", r"\bcurriculum\b", r"\binstructional\b", r"\blearning designer\b", r"\bacademic\b")),
    ("operations", (r"\boperations?\b", r"\badministrat", r"\bchief of staff\b", r"\bpeople operations\b", r"\bhuman resources\b", r"\brecruiter\b", r"\bfinance manager\b")),
)


def infer_country(location: str) -> str | None:
    # A location such as "London, England, GB" names its country; never let
    # it collapse to plain "Remote" and then a false "Worldwide".
    from jobs.services.geo import single_country

    specific = single_country(location or "")
    if specific and not re.search(r"\b(?:worldwide|anywhere|global(?:ly)?)\b", location or "", re.I):
        return specific
    normalized = normalize_location(location or "")
    if normalized in REMOTE_COUNTRIES:
        return REMOTE_COUNTRIES[normalized]
    if normalized in COUNTRY_VALUES:
        return normalized
    return None


def is_missing_value(value: Any) -> bool:
    """Treat common serialized null markers as absent structured data."""
    return value is None or (
        isinstance(value, str) and value.strip().lower() in PLACEHOLDER_VALUES
    )


def infer_category_slug(title: str, description: str = "", requirements: str = "") -> str:
    title_text = _plain_text(title)
    full_text = " ".join(
        part for part in (title_text, _plain_text(description), _plain_text(requirements)) if part
    )[:40000]

    for slug, patterns in CAUSE_CATEGORY_PATTERNS:
        if any(re.search(pattern, full_text, re.IGNORECASE) for pattern in patterns):
            return slug
    for slug, patterns in TITLE_CATEGORY_PATTERNS:
        if any(re.search(pattern, title_text, re.IGNORECASE) for pattern in patterns):
            return slug
    return "other"


def enrich_job_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Fill missing structured fields without replacing upstream values."""
    enriched = dict(payload)
    title = str(enriched.get("title") or "")
    description = str(enriched.get("description") or "")
    requirements = str(enriched.get("requirements") or "")
    location = str(enriched.get("location") or "Remote")

    source_values = RawDataExtractor.extract(
        str(enriched.get("source") or ""), enriched.get("raw_data") or {}
    )
    rejected_salary = (
        (((enriched.get("raw_data") or {}).get("quality_enrichment") or {})
        .get("salary", {}).get("method")) == "rejected_non_annual_v1"
    )
    for field in (
        "experience_level", "education_level", "country", "salary_min",
        "salary_max", "salary_currency", "job_type", "location", "posted_at",
        "expires_at",
    ):
        if field.startswith("salary_") and rejected_salary:
            continue
        if not enriched.get(field) and source_values.get(field) is not None:
            enriched[field] = source_values[field]

    # Some upstream JSON payloads serialize a missing value as the literal
    # string "null". Normalize it before classifiers and persistence see it.
    for field in ("experience_level", "education_level", "country", "salary_currency"):
        if is_missing_value(enriched.get(field)):
            enriched[field] = "" if field == "salary_currency" else None

    _normalize_source_salary(enriched)

    salary_method = (
        ((enriched.get("raw_data") or {}).get("quality_enrichment") or {})
        .get("salary", {})
        .get("method", "")
    )
    if (
        not enriched.get("salary_min")
        and not enriched.get("salary_max")
        and salary_method != "rejected_non_annual_v1"
    ):
        salary = extract_annual_salary(" ".join((requirements, description)))
        if salary:
            enriched.update({key: salary[key] for key in (
                "salary_min", "salary_max", "salary_currency"
            )})
            raw_data = dict(enriched.get("raw_data") or {})
            quality = dict(raw_data.get("quality_enrichment") or {})
            quality["salary"] = {
                "method": "deterministic_text_v1",
                "evidence": salary["salary_evidence"],
            }
            raw_data["quality_enrichment"] = quality
            enriched["raw_data"] = raw_data

    if not enriched.get("country"):
        enriched["country"] = infer_country(location)

    if not enriched.get("skills"):
        skill_text = " ".join((title, requirements, description))
        enriched["skills"] = sorted(MatchingService._extract_skills_from_text(skill_text))

    if not enriched.get("experience_level"):
        level, reason = ExperienceClassifier.get_best_classification(
            title=title,
            description=description,
            job_type=str(enriched.get("job_type") or ""),
        )
        enriched["experience_level"] = level
        if level:
            _record_quality_enrichment(enriched, "experience_level", "deterministic_text_v1", reason)

    if not enriched.get("education_level"):
        education, reason = EducationClassifier.get_best_classification(
            description=description,
            requirements=requirements,
        )
        enriched["education_level"] = education
        if education:
            _record_quality_enrichment(enriched, "education_level", "deterministic_text_v1", reason)

    if not enriched.get("category_slug") and not enriched.get("category_name"):
        enriched["category_slug"] = infer_category_slug(title, description, requirements)

    _sanitize_salary_range(enriched)
    return enriched


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def _record_quality_enrichment(
    payload: dict[str, Any], field: str, method: str, evidence: str
) -> None:
    raw_data = dict(payload.get("raw_data") or {})
    quality = dict(raw_data.get("quality_enrichment") or {})
    quality[field] = {"method": method, "evidence": evidence[:240]}
    raw_data["quality_enrichment"] = quality
    payload["raw_data"] = raw_data


def _sanitize_salary_range(payload: dict[str, Any]) -> None:
    """Keep derived salary values inside the Job model's safe annual range."""
    minimum = payload.get("salary_min")
    maximum = payload.get("salary_max")
    numeric = [value for value in (minimum, maximum) if value is not None]
    if not numeric:
        return
    if (
        any(float(value) < 0 or float(value) > 10_000_000 for value in numeric)
        or (minimum is not None and maximum is not None and float(minimum) > float(maximum))
    ):
        payload["salary_min"] = None
        payload["salary_max"] = None
        payload["salary_currency"] = ""
        _record_quality_enrichment(
            payload,
            "salary",
            "rejected_non_annual_v1",
            "derived salary fell outside the safe annual range",
        )


def _normalize_source_salary(payload: dict[str, Any]) -> None:
    """Normalize known source units and remove upstream unknown sentinels."""
    source = str(payload.get("source") or "")
    raw_data = payload.get("raw_data") or {}
    minimum = payload.get("salary_min")
    maximum = payload.get("salary_max")

    if source == "80000hours" and raw_data.get("salary"):
        source_salary = RawDataExtractor.extract(source, raw_data)
        if source_salary.get("salary_min") is not None and source_salary.get("salary_max") is not None:
            payload["salary_min"] = source_salary["salary_min"]
            payload["salary_max"] = source_salary["salary_max"]
            payload["salary_currency"] = source_salary.get("salary_currency") or ""
            return

    if source == "80000hours" and maximum is not None and float(maximum) >= 9_000_000:
        payload["salary_max"] = None
        if minimum is None:
            payload["salary_currency"] = ""
        return

    if source != "idealist" or (minimum is None and maximum is None):
        return
    period = str(raw_data.get("salaryPeriod") or "YEAR").upper()
    if period in {'HOUR','DAY','WEEK'}:
        payload['salary_min'] = None
        payload['salary_max'] = None
        _record_quality_enrichment(payload,'salary','rejected_non_annual_v1',f'{source} salaryPeriod={period}; hours unknown')
        return
    multiplier = {"MONTH": 12}.get(period, 1)
    if multiplier == 1:
        return
    if minimum is not None:
        payload["salary_min"] = float(minimum) * multiplier
    if maximum is not None:
        payload["salary_max"] = float(maximum) * multiplier
    _record_quality_enrichment(
        payload,
        "salary",
        "source_period_normalization_v1",
        f"{source} salaryPeriod={period}",
    )
