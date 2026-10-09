"""Confirm imported listings against the employer's own ATS board.

Closed postings and postings the employer marks on-site/hybrid are taken off the
site; confirmed-open postings get a fresh last_seen_at. Nothing is changed when
the ATS doesn't give a definitive answer.
"""
import json
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from jobs.models import Job
from jobs.services import ats_boards

class Command(BaseCommand):
    help = "Check live listings against employer ATS boards (Greenhouse, Lever, Ashby, Workday, ...)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=0, help="Check at most this many jobs (0 = all).")
        parser.add_argument("--ats", action="append", default=[], help="Only these ATSs (repeatable).")
        parser.add_argument("--workers", type=int, default=8)
        parser.add_argument("--report", help="Write per-job results as JSON.")
        parser.add_argument(
            "--max-remove-ratio", type=float, default=0.9,
            help="Refuse to apply if more than this share of definitive answers for one ATS would remove jobs.",
        )
        parser.add_argument("--force", action="store_true", help="Apply even if the ratio guard trips.")

    def handle(self, *args, **options):
        started = time.monotonic()
        jobs = list(
            Job.objects.filter(is_active=True).exclude(source=Job.Source.MANUAL).exclude(application_url="")
            .only("id", "organization_id", "application_url", "raw_data", "last_seen_at")
            .order_by("id")
        )
        greenhouse_by_org = {}
        for job in jobs:
            ref = ats_boards.parse(job.application_url)
            if ref and ref.ats == "greenhouse":
                greenhouse_by_org.setdefault(job.organization_id, ref.board)

        targets = []
        for job in jobs:
            host = urlparse(job.application_url).netloc.lower().split(":")[0]
            known = {host: greenhouse_by_org[job.organization_id]} if job.organization_id in greenhouse_by_org else None
            ref = ats_boards.parse(job.application_url, known)
            if not ref or (options["ats"] and ref.ats not in options["ats"]):
                continue
            targets.append((job, ref))
            if options["limit"] and len(targets) >= options["limit"]:
                break
        if not targets:
            self.stdout.write("No ATS-checkable listings.")
            return

        results, resolved = self._check(targets, options["workers"])
        targets = [(job, resolved.get(job.id, ref)) for job, ref in targets]

        by_ats = defaultdict(Counter)
        for job, ref in targets:
            check = results[job.id]
            by_ats[ref.ats][self._outcome(check)] += 1
        blocked = []
        for ats, counts in by_ats.items():
            definitive = counts["open"] + counts["closed"] + counts["not_remote"]
            removed = counts["closed"] + counts["not_remote"]
            if definitive >= 30 and removed / definitive > options["max_remove_ratio"]:
                blocked.append(ats)

        now = timezone.now()
        report = []
        applied = Counter()
        for job, ref in targets:
            check = results[job.id]
            outcome = self._outcome(check)
            entry = {
                "id": job.id, "url": job.application_url, **ref.as_dict(), "outcome": outcome,
                "workplace": check.workplace, "location": check.location, "reason": check.reason,
            }
            report.append(entry)
            if options["dry_run"] or (ref.ats in blocked and not options["force"]):
                continue
            raw = dict(job.raw_data or {})
            raw["ats"] = ref.as_dict()
            raw["ats_check"] = {
                "status": outcome, "checked_at": now.isoformat(), "workplace": check.workplace,
                "location": check.location[:200], "reason": check.reason,
            }
            fields = ["raw_data"]
            if outcome != "unknown":
                raw["source_verification"] = {
                    "status": outcome, "method": "ats_api", "ats": ref.ats, "checked_at": now.isoformat(),
                    "workplace": check.workplace, "reason": check.reason,
                }
                raw["last_stale_check"] = now.isoformat()
            if outcome == "open":
                job.last_seen_at = now
                fields.append("last_seen_at")
            elif outcome in {"closed", "not_remote"}:
                job.is_active = False
                fields.append("is_active")
            job.raw_data = raw
            Job.objects.filter(pk=job.pk).update(**{f: getattr(job, f) for f in fields})
            applied[outcome] += 1

        if options["report"]:
            Path(options["report"]).write_text(json.dumps(report, indent=2), encoding="utf-8")

        for ats in sorted(by_ats):
            counts = by_ats[ats]
            flag = "  BLOCKED by ratio guard" if ats in blocked and not options["force"] else ""
            self.stdout.write(
                f"{ats:16} open={counts['open']:5} closed={counts['closed']:5} "
                f"not_remote={counts['not_remote']:4} unknown={counts['unknown']:4}{flag}"
            )
        total = Counter()
        for counts in by_ats.values():
            total.update(counts)
        self.stdout.write(
            f"Checked {len(targets)} listings in {time.monotonic() - started:.0f}s: "
            f"open={total['open']} closed={total['closed']} not_remote={total['not_remote']} "
            f"unknown={total['unknown']}. "
            + ("Dry run: no changes." if options["dry_run"] else f"Applied: {dict(applied)}.")
        )
        if blocked and not options["force"]:
            raise CommandError(f"Ratio guard tripped for {', '.join(blocked)}; those were not applied.")

    @staticmethod
    def _outcome(check):
        return "not_remote" if ats_boards.excludes_remote(check) else check.status

    def _check(self, targets, workers):
        """Network phase: no ORM access in threads."""
        results, resolved = {}, {}
        boards = defaultdict(list)
        postings = []
        unresolved_workable = []
        for job, ref in targets:
            if ref.ats == "workable" and not ref.board:
                unresolved_workable.append((job, ref))
            elif ref.ats in ats_boards.BOARD_ATS:
                boards[ref.board_key].append((job, ref))
            else:
                postings.append((job, ref))

        local = threading.local()

        def session():
            if not hasattr(local, "s"):
                local.s = requests.Session()
            return local.s

        with ThreadPoolExecutor(max_workers=workers) as pool:
            accounts = list(pool.map(lambda jr: ats_boards.resolve_workable_account(jr[1], session()),
                                     unresolved_workable))
            for (job, ref), account in zip(unresolved_workable, accounts):
                if account:
                    resolved[job.id] = ats_boards.AtsRef("workable", account, ref.job)
                    boards[resolved[job.id].board_key].append((job, resolved[job.id]))
                else:
                    results[job.id] = ats_boards.Check("unknown", reason="workable link did not resolve")

            keys = list(boards)
            fetched = pool.map(lambda key: ats_boards.fetch_board(boards[key][0][1], session()), keys)
            for key, board in zip(keys, fetched):
                for job, ref in boards[key]:
                    results[job.id] = ats_boards.check_against_board(ref, board)

            checks = pool.map(lambda jr: ats_boards.check_posting(jr[1], session()), postings)
            for (job, ref), check in zip(postings, checks):
                results[job.id] = check
        return results, resolved
