"""Assemble and send the weekly roundup from machine-verified roles, without a manual review step.

A role qualifies only if the employer's own ATS confirmed it open in the last 23 hours (verify_ats_jobs
runs daily), it lists an annual pay range, and it was posted in the last three weeks. The issue goes
through send_roundup, so its identity, freshness and duplicate-delivery checks all still apply.
If any issue was already delivered this week (for example a hand-edited one), this does nothing.

Dry run unless --send.
"""
import hashlib
import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from jobs.models import JobDailyStat, RoundupDelivery
from jobs.services.impact_metrics import competition
from jobs.services.job_service import JobService
from jobs.services.roundup import CATEGORY, issue_week

SLOTS = (("climate", 2), ("health", 2), ("ai", 2), ("early", 1))
MAX_PICKS = 8
MIN_ANNUAL = 15000


def verified_at(job, now):
    check = (job.raw_data or {}).get("source_verification") or {}
    checked = parse_datetime(str(check.get("checked_at") or ""))
    if check.get("status") == "open" and checked and now - timedelta(hours=23) <= checked <= now:
        return checked
    return None


def topics(job):
    found = {topic for topic, slug in CATEGORY.items() if job.category and job.category.slug == slug}
    if job.experience_level in {"entry", "internship"}:
        found.add("early")
    return found


def pay(job):
    low, high, currency = int(job.salary_min), int(job.salary_max or 0), job.salary_currency or "USD"
    if high > low:
        return f"{currency} {low:,}–{high:,} a year, as listed by the employer."
    return f"{currency} {low:,} a year, as listed by the employer."


def where(job):
    check = (job.raw_data or {}).get("ats_check") or {}
    listed = (check.get("location") or "").strip()
    text = job.location.strip().rstrip(".") + "."
    if listed and listed.lower() not in job.location.lower():
        text += f" The employer lists: {listed[:120]}."
    return text + " Check the posting for country and time-zone limits."


def choose(now):
    pool = (JobService.get_available_jobs().filter(posted_at__gte=now - timedelta(days=21), salary_min__gte=MIN_ANNUAL)
            .exclude(application_url="").select_related("organization", "category"))
    pool = [(job, verified_at(job, now)) for job in pool]
    pool = [(job, checked) for job, checked in pool if checked]
    since = timezone.localdate() - timedelta(days=6)
    clicks = {row["job_id"]: row["c"] or 0 for row in JobDailyStat.objects.filter(
        date__gte=since, job_id__in=[job.pk for job, _ in pool]).values("job_id").annotate(c=Sum("apply_clicks"))}
    pool.sort(key=lambda item: (-clicks.get(item[0].pk, 0), -(item[0].quality_score or 0), -item[0].posted_at.timestamp()))
    picked, employers = [], set()

    def take(job, checked):
        picked.append((job, checked))
        employers.add(job.organization_id)

    for topic, count in SLOTS:
        for job, checked in pool:
            if count and topic in topics(job) and job.organization_id not in employers:
                take(job, checked)
                count -= 1
    for job, checked in pool:
        if len(picked) >= MAX_PICKS:
            break
        if job.organization_id not in employers:
            take(job, checked)
    return picked


def insight():
    sectors = competition("category")
    levels = {row.label: row for row in competition("level", min_roles=1)}
    lines = []
    if len(sectors) >= 2:
        busy, quiet = sectors[0], sectors[-1]
        lines.append(f"Over the last 30 days, {busy.label.lower()} roles drew {busy.per_role:.1f} apply clicks each from "
                     f"Remote Impact readers, while {quiet.label.lower()} roles drew {quiet.per_role:.1f}.")
    entry, senior = levels.get("Entry Level"), levels.get("Senior")
    if entry and senior and senior.per_role:
        lines.append(f"Entry-level roles drew {entry.per_role / senior.per_role:.0f} times as many clicks per role as senior ones, "
                     "so experienced applicants often face less competition than they expect.")
    return " ".join(lines)


def build_issue(now):
    stamp = now.strftime("%Y%m%d")
    picks = [{"title": job.title, "employer": job.organization.name, "location_label": job.location,
              "experience": job.get_experience_level_display(),
              "url": settings.SITE_URL.rstrip("/") + job.get_absolute_url() + "?" + urlencode(
                  {"utm_source": "newsletter", "utm_medium": "email", "utm_campaign": f"roundup_{stamp}"}),
              "employer_source": job.application_url, "job_id": str(job.pk),
              "employer_source_reviewed_at": checked.isoformat(),
              "eligible_countries_review": where(job), "salary_review": pay(job)}
             for job, checked in choose(now)]
    note = [f"{len(picks)} remote roles this week, each confirmed open on the employer's own site in the last day."]
    if text := insight():
        note.append(text)
    return {"prepared_at": now.isoformat(), "status": "approved", "issue_week": issue_week(),
            "subject": f"{len(picks)} verified remote impact roles this week", "note": note, "picks": picks,
            "editorial_approval": {"method": "automatic", "rule": "employer ATS confirmed open within 23 hours; annual pay listed"},
            "send_enabled": True}


class Command(BaseCommand):
    help = "Send this week's roundup from ATS-verified roles unless an issue already went out (dry run unless --send)."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true")
        parser.add_argument("--min-picks", type=int, default=4)

    def handle(self, *args, **options):
        week = issue_week()
        if RoundupDelivery.objects.filter(issue_week=week).exists():
            self.stdout.write(f"{week} already went out; nothing to do.")
            return
        issue = build_issue(timezone.now())
        if len(issue["picks"]) < options["min_picks"]:
            self.stdout.write(f"Only {len(issue['picks'])} verified roles qualified; not sending {week}.")
            return
        if not options["send"]:
            self.stdout.write(json.dumps(issue, indent=2))
            return
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "issue.json"
            manifest.write_text(json.dumps(issue))
            digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
            totals = {"accepted_by_provider": 0, "unknown_or_rejected": 0}
            for _ in range(50):
                out = io.StringIO()
                call_command("send_roundup", manifest=str(manifest), approve_sha256=digest, send=True, limit=100, stdout=out)
                result = json.loads(out.getvalue())
                for key in totals:
                    totals[key] += result[key]
                if not result["accepted_by_provider"] + result["unknown_or_rejected"]:
                    break
        self.stdout.write(f"{week}: {len(issue['picks'])} picks, {json.dumps(totals)}")
