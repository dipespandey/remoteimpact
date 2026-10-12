"""Find jobs stored as worldwide whose posting says applicants must live in one country.

The model proposes a scope with a verbatim quote; a job is changed only when the quote really appears in the posting
AND the quote itself names exactly one country (checked by code, not by the model). Dry run unless --apply.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.management.commands.label_job_fields import label
from jobs.models import Job
from jobs.services.ai import AIClient
from jobs.services.geo import single_country
from jobs.services.job_service import JobService

WORLDWIDE = {"worldwide", "global", "anywhere"}
REQUIREMENT_CUE = re.compile(
    r"\b(?:must|only|required|requires|authori[sz]ed|authori[sz]ation|eligible|reside|resident|citizen|"
    r"based in|based from|located in|located within|based within|work remotely|location\s*:|open to candidates)\b", re.I)
SOFTENER = re.compile(r"\b(?:prefer(?:red|ence)?|ideally|a plus|nice to have|remote candidates|candidates? (?:from )?anywhere)\b", re.I)


def spelled_out(quote, country):
    """The country's own name (not a two-letter state or region code) appears in the quote."""
    from jobs.services.location_normalizer import country_filter_aliases
    names = {a for a in country_filter_aliases(country) | {country} if len(a) > 3 or a.lower() in {"us", "uk", "u.s.", "u.s"}}
    names |= {"u.s.", "united states"} if country in {"USA", "United States"} else set()
    return any(re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", quote, re.I) for n in names)


def restrictive(quote, country):
    return bool(REQUIREMENT_CUE.search(quote)) and not SOFTENER.search(quote) and spelled_out(quote, country)


class Command(BaseCommand):
    help = "Mark worldwide-stored jobs whose posting names a single required country (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--workers", type=int, default=6)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--out", default="")
        parser.add_argument("--from-file", default="", help="Re-apply a saved dry-run file (re-checked by code, no model calls)")

    def handle(self, *args, **options):
        if options["from_file"]:
            saved = [c for c in json.load(open(options["from_file"])) if restrictive(c["quote"], c["country"])]
            jobs_by_pk = {j.pk: j for j in Job.objects.filter(pk__in=[c["job"] for c in saved])}
            changes = [c for c in saved if (jobs_by_pk[c["job"]].country or "").lower() in WORLDWIDE]
            self.stdout.write(f"{len(changes)} of {len(saved)} still worldwide-stored and restrictive")
            if options["apply"]:
                self._apply(changes, jobs_by_pk)
            return
        jobs = [j for j in JobService.get_available_jobs().order_by("pk") if (j.country or "").lower() in WORLDWIDE]
        if options["limit"]:
            jobs = jobs[: options["limit"]]
        client = AIClient()
        with ThreadPoolExecutor(max_workers=options["workers"]) as pool:
            labels = [r for r in pool.map(lambda j: label(client, j), jobs) if r]
        by_pk = {j.pk: j for j in jobs}
        changes, skipped = [], {"model_unstated": 0, "no_single_country_in_quote": 0, "regional": 0}
        for row in labels:
            scope = row["remote_scope"]
            if scope == "unstated" or scope == "worldwide":
                skipped["model_unstated"] += 1
                continue
            if scope == "regional":
                skipped["regional"] += 1
                continue
            country = single_country(row["remote_quote"])
            if not country or not restrictive(row["remote_quote"], country):
                skipped["no_single_country_in_quote"] += 1
                continue
            changes.append({"job": row["job"], "title": row["title"], "country": country, "quote": row["remote_quote"]})
        self.stdout.write(f"checked {len(labels)}/{len(jobs)} worldwide-stored jobs; {len(changes)} restricted to one country; skipped {skipped}")
        if options["out"]:
            json.dump(changes, open(options["out"], "w"), indent=1, ensure_ascii=False)
        if options["apply"]:
            self._apply(changes, by_pk)

    def _apply(self, changes, by_pk):
        for change in changes:
            job = by_pk[change["job"]]
            raw = dict(job.raw_data or {})
            raw["country_evidence"] = {"previous": job.country, "quote": change["quote"], "method": "llm_grounded_v1",
                                       "at": timezone.now().isoformat()}
            Job.objects.filter(pk=job.pk).update(country=change["country"], raw_data=raw)
        self.stdout.write(f"applied {len(changes)}")
