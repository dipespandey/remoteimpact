"""Measure ranking quality on real searches, so changes are compared on numbers, not impressions.

Two test sets, sampled with a fixed seed so runs are comparable:
  seekers   real Scout briefs -> the jobs Scout would send (top 10)
  employers live jobs        -> the candidates we would suggest (top 10)

An AI judge grades each pair 2 (strong fit), 1 (partial) or 0 (wrong), and must quote the text that
supports the grade; grades whose quote isn't really in the text are reported as unverified. Grades are
cached (EVAL_DIR/judgments.json), so re-runs only judge pairs they haven't seen. Read-only for the site.

Metrics per set: nDCG@10 (order quality), P@5 (share of strong fits in the top 5), Bad@10 (share of
clearly wrong results in the top 10), and coverage (queries that returned anything).
"""
import json
import math
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

EVAL_DIR = Path(os.environ.get("EVAL_DIR", "/app/var/evals"))

JUDGE_SYSTEM = (
    "You judge how well one item fits one request on a remote impact jobs platform. Grade 2 if it clearly fits the "
    "request's role/field AND does not break any stated constraint (location eligibility, seniority, job type, pay); "
    "1 if it is related but a weaker or partial fit; 0 if it is the wrong kind of role/person or breaks a stated "
    "constraint. Judge only from the text given. Give a verbatim quote (6-200 characters) copied exactly from the "
    "ITEM text that best supports your grade. "
    'Return JSON only: {"grade":0|1|2,"quote":"...","reason":"<= 20 words"}'
)


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _json(text):
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1]) if start >= 0 and end > start else {}


def job_text(job):
    from jobs.services.scout_constraints import plain_job_field

    pay = ""
    if job.salary_min or job.salary_max:
        pay = f"{job.salary_currency} {int(job.salary_min or 0)}-{int(job.salary_max or 0)}"
    body = " ".join(plain_job_field(job, f) for f in ("description", "requirements"))[:3500]
    return (f"Title: {job.title}\nOrganization: {job.organization.name}\nLocation: {job.location}\n"
            f"Type: {job.get_job_type_display()}\nExperience: {job.experience_level or 'unstated'}\nPay: {pay or 'unstated'}\n"
            f"Posting: {body}")


def ndcg(grades, k=10):
    def dcg(values):
        return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(values[:k]))
    ideal = dcg(sorted(grades, reverse=True))
    return dcg(grades) / ideal if ideal else 0.0


class Command(BaseCommand):
    help = "Evaluate seeker->job and job->candidate ranking with grounded AI judgments."

    def add_arguments(self, parser):
        parser.add_argument("--set", choices=["seekers", "employers", "both"], default="both")
        parser.add_argument("--queries", type=int, default=60)
        parser.add_argument("--jobs", type=int, default=30)
        parser.add_argument("--label", default="baseline", help="Name for this run in the results file.")
        parser.add_argument("--workers", type=int, default=8)
        parser.add_argument("--candidate-gate", choices=["none", "evidence"], default="none",
                            help="employers: keep only people whose profile shows >= --min-evidence must-haves")
        parser.add_argument("--min-evidence", type=int, default=2)

    def handle(self, *args, **options):
        from jobs.services import embedding_service
        embedding_service.load_for_batch()
        EVAL_DIR.mkdir(parents=True, exist_ok=True)
        self.cache_path = EVAL_DIR / "judgments.json"
        self.cache = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        from jobs.services.ai import AIClient
        self.client = AIClient()
        self.workers = options["workers"]
        self.gate, self.min_evidence = options["candidate_gate"], options["min_evidence"]
        results = {"label": options["label"], "at": timezone.now().isoformat()}
        if options["set"] in ("seekers", "both"):
            results["seekers"] = self.seekers(options["queries"])
        if options["set"] in ("employers", "both"):
            results["employers"] = self.employers(options["jobs"])
        self.cache_path.write_text(json.dumps(self.cache))
        with open(EVAL_DIR / "runs.jsonl", "a") as handle:
            handle.write(json.dumps(results) + "\n")
        self.stdout.write(json.dumps(results, indent=2))

    # -- judging -----------------------------------------------------------------------------------

    def judge(self, key, request_text, item_text):
        if key in self.cache:
            return self.cache[key]
        prompt = json.dumps({"request": request_text[:2500], "item": item_text[:4500]}, ensure_ascii=False)
        verdict = {"grade": None}
        for _ in range(2):
            try:
                raw = _json(self.client.generate(prompt, max_tokens=200, temperature=0, timeout=45, max_retries=1,
                                                 system_message=JUDGE_SYSTEM))
                grade = int(raw.get("grade"))
                if grade in (0, 1, 2):
                    quote = str(raw.get("quote", ""))
                    verdict = {"grade": grade, "verified": bool(quote) and _norm(quote) in _norm(item_text),
                               "reason": str(raw.get("reason", ""))[:160]}
                    break
            except Exception:  # noqa: BLE001
                continue
        if verdict["grade"] is not None:
            self.cache[key] = verdict
        return verdict

    def judge_many(self, pairs):
        def run(pair):
            close_old_connections()
            return self.judge(*pair)
        with ThreadPoolExecutor(self.workers) as pool:
            return list(pool.map(run, pairs))

    def summarize(self, per_query):
        """per_query: list of lists of verdicts (ranked)."""
        answered = [q for q in per_query if q]
        graded = [[v["grade"] for v in q if v.get("grade") is not None] for q in answered]
        flat = [v for q in answered for v in q if v.get("grade") is not None]
        if not graded:
            return {"queries": len(per_query), "coverage": 0}
        return {
            "queries": len(per_query),
            "coverage": round(len(answered) / len(per_query), 3),
            "ndcg@10": round(sum(ndcg(g) for g in graded) / len(graded), 3),
            "p@5": round(sum(sum(1 for x in g[:5] if x == 2) / max(1, min(5, len(g))) for g in graded) / len(graded), 3),
            "bad@10": round(sum(sum(1 for x in g[:10] if x == 0) / max(1, min(10, len(g))) for g in graded) / len(graded), 3),
            "judged": len(flat),
            "unverified": round(sum(1 for v in flat if not v.get("verified")) / max(1, len(flat)), 3),
        }

    # -- seekers: Scout briefs -> jobs ----------------------------------------------------------------

    def seekers(self, n):
        from jobs.models import JobAlert
        from jobs.services.job_scout_service import JobScoutService

        alerts = list(JobAlert.objects.exclude(intent_text="").order_by("pk"))
        random.Random(42).shuffle(alerts)
        since = timezone.now() - timedelta(days=90)
        per_query, examples = [], []
        for alert in alerts[:n]:
            try:
                # Rank the whole recent inventory, not just jobs this person hasn't been sent yet.
                matches = JobScoutService.get_matches_for_preferences(
                    JobScoutService.preferences_for_alert(alert), since=since, limit=10)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"alert {alert.pk}: {exc}")
                matches = []
            jobs = [getattr(m, "job", m) for m in matches][:10]
            verdicts = self.judge_many([(f"s:{alert.pk}:{job.pk}", alert.intent_text, job_text(job)) for job in jobs])
            per_query.append(verdicts)
            bad = [(job.title, v.get("reason")) for job, v in zip(jobs, verdicts) if v.get("grade") == 0]
            if bad:
                examples.append({"brief": alert.intent_text[:140], "wrong": bad[:3]})
        summary = self.summarize(per_query)
        summary["wrong_examples"] = examples[:8]
        return summary

    # -- employers: jobs -> suggested candidates -------------------------------------------------------

    def employers(self, n):
        from jobs.models import Job
        from jobs.services.candidate_suggestions import suggested_candidates
        from jobs.services.hiring_assistant import profile_material

        jobs = [j for j in Job.objects.filter(is_active=True).exclude(embedding=None)
                .select_related("organization").order_by("pk") if j.is_available]
        random.Random(42).shuffle(jobs)
        per_query = []
        for job in jobs[:n]:
            if self.gate == "evidence":
                from jobs.services.hiring_assistant import ensure_must_haves, sourcing_evidence

                ensure_must_haves(job, self.client)  # once, before the parallel checks
                pool = suggested_candidates(job, limit=20)

                def gate(s):
                    close_old_connections()
                    return s, len(sourcing_evidence(job, s.profile, self.client))
                with ThreadPoolExecutor(self.workers) as executor:
                    scored = list(executor.map(gate, pool))
                people = [s for s, met in sorted(scored, key=lambda x: -x[1]) if met >= self.min_evidence][:10]
            else:
                people = suggested_candidates(job, limit=10)
            per_query.append(self.judge_many([
                (f"e:{job.pk}:{s.profile.pk}", job_text(job), profile_material(s.profile)) for s in people]))
        return self.summarize(per_query)
