"""Verify source pages without treating blocked requests as live openings."""

import json
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F
from django.db.models.fields.json import KeyTextTransform
from django.utils import timezone

from jobs.models import Job
from jobs.services import ats_boards
from jobs.services.source_page_enrichment import fetch_job_page, page_matches_title


class Command(BaseCommand):
    help = "Check stale source links; deactivate only definitive 404/410 responses."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument("--delay", type=float, default=0.5)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--cooldown-days", type=int, default=7)
        parser.add_argument("--report", help="Write per-job results before applying changes.")

    def handle(self, *args, **options):
        if options["limit"] < 1 or options["delay"] < 0 or options["cooldown_days"] < 0:
            raise CommandError("Limit must be positive; delay/cooldown must be nonnegative.")
        cutoff = timezone.now() - timedelta(days=options["cooldown_days"])
        jobs = Job.objects.filter(is_active=True).exclude(
            source=Job.Source.MANUAL,
        ).exclude(application_url="").annotate(
            last_checked=KeyTextTransform("last_stale_check", "raw_data"),
        ).order_by(F("last_checked").asc(nulls_first=True), "posted_at", "id")
        results = []
        for job in jobs.iterator(chunk_size=200):
            raw = job.raw_data or {}
            if ats_boards.parse(job.application_url):
                continue  # verify_ats_jobs asks the employer's ATS directly
            try:
                checked = datetime.fromisoformat(str(raw.get("last_stale_check", "")).replace("Z", "+00:00"))
                if timezone.is_naive(checked):
                    checked = timezone.make_aware(checked)
                if checked > cutoff:
                    continue
            except (ValueError, TypeError):
                pass
            page = fetch_job_page(job.application_url, title=job.title)
            if page.status in {"http_404", "http_410"}:
                status = "dead"
            elif page.status == "ok" and page_matches_title(job.title, page.text):
                status = "title_confirmed"
            else:
                status = "unverified"
            results.append({
                "id": job.id, "title": job.title, "source": job.source,
                "url": job.application_url, "status": status, "response": page.status,
                "was_active": job.is_active, "checked_at": timezone.now().isoformat(),
                "opening_status": "closed" if status == "dead" else "unknown",
                "evidence_kind": "http_status" if status == "dead" else "page_title" if status == "title_confirmed" else "none",
            })
            if len(results) >= options["limit"]:
                break
            time.sleep(options["delay"])

        if options["report"]:
            Path(options["report"]).write_text(json.dumps(results, indent=2), encoding="utf-8")
        if not options["dry_run"]:
            for result in results:
                job = Job.objects.get(pk=result["id"])
                raw = dict(job.raw_data or {})
                raw["last_stale_check"] = result["checked_at"]
                raw["source_verification"] = result
                fields = ["raw_data"]
                if result["status"] == "dead":
                    job.is_active = False
                    raw["crawl_error"] = result["response"]
                    fields.append("is_active")
                job.raw_data = raw
                job.save(update_fields=fields)
        counts = Counter(item["status"] for item in results)
        self.stdout.write(
            f"Checked {len(results)}: title_confirmed={counts['title_confirmed']}, "
            f"dead={counts['dead']}, unverified={counts['unverified']}. "
            + ("Dry run: no database changes." if options["dry_run"] else "Recorded checks; only dead links deactivated.")
        )
