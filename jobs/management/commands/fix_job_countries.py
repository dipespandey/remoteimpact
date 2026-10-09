"""Correct jobs labelled "Worldwide" whose own listing names a specific country.

Only high-confidence corrections are made:
  * the location field names exactly one country (or US state), e.g.
    "London, England, GB" or "California, US";
  * a plain "Remote" listing whose text restricts residency to one country,
    e.g. "must be based in the United States".
Everything else is left as it is. Each change is recorded in
raw_data["country_correction"] so it can be audited or reversed.

Usage:
    python manage.py fix_job_countries            # dry run (default)
    python manage.py fix_job_countries --apply
"""
import re
from collections import Counter

from django.core.management.base import BaseCommand
from django.utils import timezone
from jobs.models import Job
from jobs.services.geo import single_country
from jobs.services.scout_constraints import WORLD

RESIDENCY = re.compile(
    r"(?:must|need to|required to|should)\s+(?:be\s+)?(?:based|reside|located|living)\s+in\s+(?:the\s+)?([A-Za-z .]{2,40})"
    r"|(?:eligible|authori[sz]ed|legally able)\s+to\s+work\s+in\s+(?:the\s+)?([A-Za-z .]{2,40})"
    r"|\b(US|U\.S\.|UK)[- ](?:based|only)\b",
    re.I,
)



class Command(BaseCommand):
    help = "Replace a false 'Worldwide' country with the specific country the listing names."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **options):
        jobs = Job.objects.filter(Job.availability_filter(include_quality=False), country__iexact="Worldwide").only(
            "pk", "location", "description", "requirements", "country", "raw_data"
        )
        if options["limit"]:
            jobs = jobs[: options["limit"]]
        changes, reasons = [], Counter()
        for job in jobs:
            location = job.location or ""
            if any(re.search(rf"\b{word}\b", location, re.I) for word in WORLD):
                continue
            country = single_country(location)
            reason = "location_names_country"
            if not country and re.fullmatch(r"\s*remote\s*", location, re.I):
                body = f"{job.description or ''} {job.requirements or ''}"
                if re.search(r"\b(?:anywhere|worldwide|any country|globally)\b", body, re.I):
                    continue
                found = {single_country(" ".join(g for g in m.groups() if g)) for m in RESIDENCY.finditer(body)}
                found.discard(None)
                if len(found) == 1:
                    country = found.pop()
                    reason = "listing_restricts_residency"
            if country and country != job.country:
                changes.append((job, country, reason))
                reasons[reason] += 1
        self.stdout.write(f"{len(changes)} corrections: {dict(reasons)}")
        for job, country, reason in changes[:15]:
            self.stdout.write(f"  {job.pk}: {job.location!r} -> {country} ({reason})")
        if not options["apply"]:
            self.stdout.write("Dry run; pass --apply to save.")
            return
        now = timezone.now().isoformat()
        for job, country, reason in changes:
            raw = dict(job.raw_data or {})
            raw["country_correction"] = {"from": job.country, "to": country, "reason": reason, "at": now}
            Job.objects.filter(pk=job.pk).update(country=country, raw_data=raw)
        self.stdout.write(self.style.SUCCESS(f"Applied {len(changes)} corrections."))
