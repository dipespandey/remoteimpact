"""Public aggregate observations; no inference calls or applicant eligibility claims."""
from collections import Counter

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.urls import reverse
from django.utils import timezone
from django_countries import countries

from jobs.services.job_service import JobService

CACHE_SECONDS = 300
REPORT_SLUG = "nonprofit-hiring-locations"
LABELS = {
    "country": "Recognised country label",
    "worldwide": "Worldwide-style label",
    "other": "Other or multiple-location label",
    "unknown": "No country label recorded",
}
WORLDWIDE_LABELS = {"worldwide", "global", "anywhere", "remote worldwide", "remote (worldwide)"}
ALIASES = {"usa": "US", "u.s.": "US", "united states": "US", "uk": "GB", "u.k.": "GB"}


def location_label(value):
    """Normalise exact labels only. Never expand a region into eligible countries."""
    label = " ".join(str(value or "").split())
    if not label:
        return "unknown", "Not recorded"
    if label.casefold() in WORLDWIDE_LABELS:
        return "worldwide", label
    names = {str(name).casefold(): code for code, name in countries}
    code = ALIASES.get(label.casefold()) or names.get(label.casefold())
    if not code and label == label.upper() and label in dict(countries):
        code = label.upper()
    if code:
        return "country", str(countries.name(code))
    return "other", label


def build_snapshot():
    # Reuse the public destination's scope, including its explicitly labelled
    # source/name candidates. This is not a registry of legally verified charities.
    from jobs.views.seo_pages import nonprofit_inventory

    qs = nonprofit_inventory(JobService.get_available_jobs())
    grouped = list(qs.values("country", "organization_id", "organization__organization_type",
                             "source", "raw_data__orgType")
                   .annotate(total=Count("pk")).order_by())
    groups = Counter({key: 0 for key in LABELS})
    labels = Counter()
    basis = Counter({"recorded_type": 0, "source_classification": 0, "name_candidate": 0})
    employers = set()
    for row in grouped:
        count = row["total"]
        kind, label = location_label(row["country"])
        groups[kind] += count
        labels[(kind, label)] += count
        employers.add(row["organization_id"])
        if row["organization__organization_type"] in {"nonprofit", "foundation"}:
            basis["recorded_type"] += count
        elif row["source"] == "idealist" and row["raw_data__orgType"] == "NONPROFIT":
            basis["source_classification"] += count
        else:
            basis["name_candidate"] += count
    total = sum(groups.values())
    site = settings.SITE_URL.rstrip("/")
    page_path = reverse("jobs:nonprofit_hiring_locations")
    search_path = reverse("jobs:keyword_jobs", kwargs={"keyword_slug": "remote-nonprofit-jobs"})
    examples = []
    for job in qs.select_related("organization").order_by("-posted_at", "-pk")[:8]:
        kind, label = location_label(job.country)
        examples.append({"title": job.title, "employer": job.organization.name,
                         "url": site + job.get_absolute_url(), "location": job.location,
                         "country_label": label, "label_group": kind})
    return {
        "schema_version": 1,
        "observed_at": timezone.now().isoformat(),
        "refresh_interval_seconds": CACHE_SECONDS,
        "url": site + page_path,
        "data_url": site + reverse("jobs:nonprofit_hiring_locations_data"),
        "search_url": site + search_path,
        "publisher": "Remote Impact",
        "title": "Remote nonprofit jobs: location data",
        "unit": "catalogue listing records",
        "total_listings": total,
        "total_employers": len(employers),
        "location_groups": [{"key": key, "label": label, "listings": groups[key],
                             "percent": round(100 * groups[key] / total, 1) if total else None}
                            for key, label in LABELS.items()],
        "location_labels": [{"group": kind, "label": label, "listings": count}
                            for (kind, label), count in sorted(labels.items(), key=lambda x: (-x[1], x[0]))],
        "classification_basis": dict(basis),
        "examples": examples,
        "methodology": [
            "Scope matches Remote Impact's unfiltered remote nonprofit jobs search and public listing availability rules.",
            "Nonprofit scope includes recorded nonprofit/foundation employers, unclassified employers labelled NONPROFIT by Idealist, and remaining unclassified employers with explicit nonprofit/NGO/charity/foundation names. These are discovery classifications, not independent legal-status verification.",
            "Each listing contributes once to one location group. Exact country names/codes and a small alias set are normalised; regions and multiple-country strings stay in other. Only exact worldwide-style labels enter that group. Blank country fields stay unknown even if other fields contain a location.",
            "Counts describe stored country labels, not a new reading of every employer's residency, work-authorisation, time-zone, travel or application-form rules. A worldwide label does not prove unrestricted hiring; a missing label does not mean worldwide.",
            "Listing records can include regional variants of one opening; employers are counted by catalogue organisation ID. This source sample is not a census, a time series, or a measure of unique vacancies, applicants or hires.",
            "A source sighting or a public listing is not confirmation that the employer is still accepting applications. Check the original employer posting linked from each job page.",
        ],
        "attribution": "When reusing these figures, link to the report, include observed_at and the denominator, and retain the location and classification limitations.",
    }


def get_snapshot():
    return cache.get_or_set("nonprofit-hiring-locations:v1", build_snapshot, CACHE_SECONDS)
