"""Replay saved Scout briefs through the matcher and report quality.

Read-only: everything runs inside a transaction that is rolled back.
Optionally asks the configured LLM to judge each (brief, job) pair.

Usage:
    python manage.py scout_eval --out /tmp/eval.json
    python manage.py scout_eval --judge --limit 80 --out /tmp/eval.json
"""
import json
import time
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from jobs.models import JobAlert
from jobs.services.job_scout_service import JobScoutService

JUDGE_PROMPT = """You check whether a job matches what a job seeker asked for.
Seeker's brief:
<brief>{brief}</brief>

Job:
<job>Title: {title}
Organization: {org}
Location: {location}
Excerpt: {excerpt}</job>

Answer with one JSON object: {{"verdict": "relevant" | "partial" | "irrelevant", "why": "<12 words>"}}.
"relevant" = a reasonable person with this brief would want to see this job.
"partial" = adjacent field or level, might be worth a look.
"irrelevant" = wrong field, wrong kind of role, or violates an explicit exclusion."""


def judge(client, brief, job):
    excerpt = " ".join((job.description or "").split())[:700]
    prompt = JUDGE_PROMPT.format(
        brief=brief[:1200], title=job.title, org=job.organization.name,
        location=job.location or "", excerpt=excerpt,
    )
    last = ""
    for _ in range(2):
        try:
            text = client.generate(prompt, max_tokens=120, temperature=0, timeout=30, max_retries=1,
                                   system_message="You are a strict, fair relevance judge. Reply with JSON only.")
            raw = json.loads(text[text.find("{"): text.rfind("}") + 1])
            verdict = str(raw.get("verdict", "")).lower()
            if verdict in ("relevant", "partial", "irrelevant"):
                return verdict, str(raw.get("why", ""))[:120]
        except Exception as exc:  # noqa: BLE001 - evaluation must not stop on one bad call
            last = str(exc)[:80]
            time.sleep(1)
    return "error", last


class Command(BaseCommand):
    help = "Replay saved Scout briefs through the matcher (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="Lookback window, like an initial Scout.")
        parser.add_argument("--limit", type=int, default=0, help="Only evaluate the N most recent briefs.")
        parser.add_argument("--judge", action="store_true", help="Ask the LLM to judge each match.")
        parser.add_argument("--out", default="")
        parser.add_argument("--semantic", action="store_true", help="Load the embedding model so semantic retrieval runs.")

    def handle(self, *args, **options):
        if options["semantic"]:
            from jobs.services import embedding_service
            embedding_service.get_model()
        client = None
        if options["judge"]:
            from jobs.services.ai import AIClient
            client = AIClient()
        alerts = JobAlert.objects.exclude(intent_text="").exclude(intent_text__isnull=True).exclude(
            user__username="dipesh"
        ).exclude(user__username__istartswith="qa").order_by("-created_at")
        if options["limit"]:
            alerts = alerts[: options["limit"]]
        since = timezone.now() - timedelta(days=options["days"])
        rows, verdicts, reasons = [], Counter(), Counter()
        started = time.time()
        # One short, rolled-back transaction per brief: a long-lived transaction
        # would block schema changes (and then every query) on the live site.
        for alert in alerts:
            with transaction.atomic():
                diagnostics = {}
                try:
                    preferences = JobScoutService.preferences_for_alert(alert)
                    matches = JobScoutService.get_matches_for_preferences(
                        preferences, since=since, limit=5, diagnostics=diagnostics
                    )
                except Exception as exc:  # noqa: BLE001
                    rows.append({"alert": alert.pk, "error": str(exc)[:200]})
                    continue
                row = {
                    "alert": alert.pk,
                    "brief": alert.intent_text[:300],
                    "count": len(matches),
                    "hard_rejections": diagnostics.get("hard_rejections", {}),
                    "rejected": {k: diagnostics.get(k, 0) for k in ("location_rejected", "experience_rejected", "title_rejected", "skill_evidence_rejected", "score_rejected")},
                    "candidates": diagnostics.get("candidate_count", 0),
                    "unresolved": preferences.get("unresolved_constraints") or [],
                    "matches": [],
                }
                if not matches:
                    for key, value in (diagnostics.get("hard_rejections") or {}).items():
                        reasons[key] += value
                for job in matches:
                    item = {"job": job.pk, "title": job.title, "org": job.organization.name, "score": getattr(job, "scout_score", None)}
                    if client:
                        item["verdict"], item["why"] = judge(client, alert.intent_text, job)
                        verdicts[item["verdict"]] += 1
                    row["matches"].append(item)
                rows.append(row)
                transaction.set_rollback(True)
        evaluated = [r for r in rows if "error" not in r]
        empty = sum(1 for r in evaluated if r["count"] == 0)
        judged = verdicts["relevant"] + verdicts["partial"] + verdicts["irrelevant"]
        summary = {
            "briefs": len(evaluated),
            "errors": len(rows) - len(evaluated),
            "empty": empty,
            "empty_rate": round(empty / max(1, len(evaluated)), 3),
            "avg_matches": round(sum(r["count"] for r in evaluated) / max(1, len(evaluated)), 2),
            "verdicts": dict(verdicts),
            "precision_relevant": round(verdicts["relevant"] / judged, 3) if judged else None,
            "precision_relevant_or_partial": round((verdicts["relevant"] + verdicts["partial"]) / judged, 3) if judged else None,
            "top_hard_rejections_in_empty": reasons.most_common(12),
            "seconds": round(time.time() - started, 1),
        }
        self.stdout.write(json.dumps(summary, indent=2))
        if options["out"]:
            with open(options["out"], "w") as fh:
                json.dump({"summary": summary, "rows": rows}, fh, indent=1, default=str)
