"""Import remote roles straight from employers' own job boards.

Aggregators are how we discover impact employers; the employer's ATS is where the
listing actually lives. For every supported ATS board (see ats_boards.LISTING_ATS) we have seen in an
application link, fetch the whole board and import the postings the employer marks
as remote. Each one starts out verified (it came from the employer's board) and
verify_ats_jobs keeps it honest afterwards.
"""
import asyncio
from datetime import datetime, timezone as dt_timezone
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from jobs.models import Job, Organization
from jobs.services import ats_boards
from jobs.services.crawlers.base import extract_salary_from_text, html_to_markdown
from jobs.services.importers.common import FEED_SOURCES, _map_job_type, batch_upsert_jobs

SOURCES = {
    "greenhouse": Job.Source.GREENHOUSE, "lever": Job.Source.LEVER, "ashby": Job.Source.ASHBY,
    "workable": Job.Source.WORKABLE, "recruitee": Job.Source.RECRUITEE, "personio": Job.Source.PERSONIO,
    "bamboohr": Job.Source.BAMBOOHR,
}
MIN_DESCRIPTION_CHARS = 400


class Command(BaseCommand):
    help = "Import remote postings directly from known employer ATS boards (Greenhouse, Lever, Ashby, Workable, Recruitee, Personio, BambooHR)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--ats", action="append", default=[], choices=sorted(ats_boards.LISTING_ATS))
        parser.add_argument("--limit-boards", type=int, default=0)
        parser.add_argument("--max-per-board", type=int, default=40,
                            help="Cap per employer so one large company can't dominate the board.")
        parser.add_argument("--workers", type=int, default=8)

    def handle(self, *args, **options):
        started = time.monotonic()
        registry = self._registry(options["ats"])
        if options["limit_boards"]:
            registry = dict(list(registry.items())[: options["limit_boards"]])
        self.stdout.write(f"Fetching {len(registry)} employer boards...")

        local = threading.local()

        def fetch(key):
            if not hasattr(local, "session"):
                local.session = requests.Session()
            return ats_boards.fetch_listings(registry[key]["ref"], local.session)

        keys = list(registry)
        with ThreadPoolExecutor(max_workers=options["workers"]) as pool:
            results = dict(zip(keys, pool.map(fetch, keys)))

        now = timezone.now().isoformat()
        payloads, counts, capped = [], Counter(), Counter()
        for key, listings in results.items():
            if listings is None:
                counts["boards_failed"] += 1
                continue
            counts["boards_ok"] += 1
            entry = registry[key]
            kept = 0
            for listing in listings:
                counts["postings"] += 1
                if not ats_boards.is_remote_listing(listing):
                    counts["not_remote"] += 1
                    continue
                description = html_to_markdown(listing.description_html)
                if len(description.strip()) < MIN_DESCRIPTION_CHARS or not listing.title.strip() or not listing.url:
                    counts["thin"] += 1
                    continue
                if kept >= options["max_per_board"]:
                    counts["over_cap"] += 1
                    capped[entry["org_name"]] += 1
                    continue
                kept += 1
                payloads.append(self._payload(entry, listing, description, now))
        counts["remote_candidates"] = len(payloads)

        per_board = Counter(p["organization_name"] for p in payloads)
        self.stdout.write("Largest boards: " + ", ".join(f"{name} {n}" for name, n in per_board.most_common(15)))
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        if options["dry_run"]:
            self.stdout.write("Over cap: " + ", ".join(f"{name} +{n}" for name, n in capped.most_common(15)))
            self.stdout.write(f"Dry run: {summary}")
            for payload in payloads[:10]:
                self.stdout.write(f"  {payload['organization_name']}: {payload['title']} ({payload['location']})")
            return

        stats = asyncio.run(batch_upsert_jobs(payloads, use_ai=False, skip_existing=True))
        self.stdout.write(
            f"{summary}. Imported: created {stats.get('created', 0)}, updated {stats.get('updated', 0)}, "
            f"skipped {stats.get('skipped', 0)}, failed {stats.get('failed', 0)} "
            f"in {time.monotonic() - started:.0f}s."
        )

    def _registry(self, only):
        """Boards of employers that a curated impact feed has listed (or staff verified).

        Boards only discovered by web search are not trusted: that is how staffing agencies,
        resellers and unrelated companies used to get in.
        """
        boards = {}
        orgs = defaultdict(Counter)
        categories = defaultdict(Counter)
        trusted = Job.objects.filter(source__in=FEED_SOURCES).values("organization_id")
        rows = (
            Job.objects.exclude(application_url="")
            .exclude(organization__name__iexact="Unknown Organization")
            .exclude(organization__verification_status=Organization.VerificationStatus.REJECTED)
            .filter(
                Q(organization_id__in=trusted)
                | Q(organization__verification_status=Organization.VerificationStatus.VERIFIED)
            )
            .values_list("application_url", "organization__name", "category__name", "raw_data__ats__board")
        )
        for url, org_name, category_name, known_board in rows.iterator(chunk_size=2000):
            ref = ats_boards.parse(url)
            if ref and ref.ats == "workable" and not ref.board:
                # Short apply.workable.com/j/CODE links don't name the account; the daily
                # check records it once resolved.
                ref = ats_boards.AtsRef("workable", known_board, ref.job) if known_board else None
            if not ref or ref.ats not in ats_boards.LISTING_ATS or (only and ref.ats not in only):
                continue
            boards.setdefault(ref.board_key, ref)
            orgs[ref.board_key][org_name] += 1
            if category_name:
                categories[ref.board_key][category_name] += 1
        return {
            key: {
                "ref": ref,
                "org_name": orgs[key].most_common(1)[0][0],
                "category_name": categories[key].most_common(1)[0][0] if categories[key] else None,
            }
            for key, ref in boards.items()
        }

    @staticmethod
    def _payload(entry, listing, description, checked_at):
        ref = entry["ref"]
        salary = extract_salary_from_text(listing.salary_text) or extract_salary_from_text(description) or {}
        posted = parse_datetime(listing.posted_at) if listing.posted_at else None
        if posted is None and listing.posted_at:
            day = parse_date(listing.posted_at[:10])
            posted = datetime.combine(day, datetime.min.time()) if day else None
        if posted is not None and timezone.is_naive(posted):
            posted = timezone.make_aware(posted, dt_timezone.utc)
        return {
            "source": SOURCES[ref.ats],
            # BambooHR numbers postings independently on each company board.
            # A bare "60" must never overwrite another employer's job #60.
            "external_id": f"{ref.board.lower()}:{listing.job}" if ref.ats == 'bamboohr' else listing.job,
            "title": listing.title.strip(),
            "description": description,
            "requirements": description,
            "location": listing.location or "Remote",
            "job_type": _map_job_type(listing.employment_type),
            "application_url": listing.url,
            "application_email": "",
            "salary_min": salary.get("salary_min"),
            "salary_max": salary.get("salary_max"),
            "salary_currency": salary.get("salary_currency"),
            "posted_at": posted or timezone.now(),
            "expires_at": None,
            "category_name": entry["category_name"],
            "organization_name": entry["org_name"],
            "organization_description": "",
            "organization_url": "",
            "raw_data": {
                "ats": {**ref.as_dict(), "job": listing.job},
                "imported_from": "employer_board",
                "posted_at_basis": "source" if posted is not None else "first_seen",
                "source_verification": {
                    "status": "open", "method": "ats_api", "ats": ref.ats, "checked_at": checked_at,
                    "workplace": listing.workplace, "reason": "listed on employer board",
                },
            },
        }
