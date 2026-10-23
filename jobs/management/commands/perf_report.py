"""Summarize privacy-safe performance JSONL logs without modifying them."""
import json
import math
import time
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_LINES = 100_000
ALLOWED_STAGES = {"workflow", "parse", "match", "retrieval", "embedding", "plan", "refinement",
                  "reranker", "close_matches", "source_profile", "remote_policy", "model", "plan_cache"}


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    # Nearest-rank percentile, retaining an observed duration as the result.
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def read_events(path):
    path = Path(path)
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - MAX_INPUT_BYTES))
            if size > MAX_INPUT_BYTES:
                stream.readline()  # discard a possible partial first record
            tail = stream.read(MAX_INPUT_BYTES)
            lines = tail.splitlines()
    except OSError as exc:
        raise CommandError("Cannot read performance log") from exc
    for raw in lines[-MAX_LINES:]:
        try:
            event = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict):
            yield event


def summarize(events):
    routes = defaultdict(lambda: {"seconds": [], "cache": defaultdict(int), "outcome": defaultdict(int), "cache_seconds": defaultdict(list)})
    stages = defaultdict(lambda: {"seconds": [], "outcome": defaultdict(int)})
    calls = []
    for event in events:
        kind = event.get("kind")
        if kind == "request" and isinstance(event.get("route"), str):
            row = routes[event["route"][:120]]
            duration = event.get("seconds")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration >= 0:
                row["seconds"].append(float(duration))
                cache_state = event.get("cache", "unknown")
                row["cache_seconds"][cache_state if cache_state in {"hit", "fresh", "stale"} else "unknown"].append(float(duration))
            row["cache"][event.get("cache", "unknown") if event.get("cache") in {"hit", "fresh", "stale"} else "unknown"] += 1
            row["outcome"][event.get("outcome", "unknown") if event.get("outcome") in {"ok", "error", "partial"} else "unknown"] += 1
        elif kind == "scout":
            if isinstance(event.get("calls"), int) and not isinstance(event.get("calls"), bool):
                calls.append(event["calls"])
            duration = event.get("seconds")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration >= 0:
                row = stages["workflow"]
                row["seconds"].append(float(duration))
                row["outcome"][event.get("outcome", "unknown")] += 1
            for item in event.get("stages", []) if isinstance(event.get("stages"), list) else []:
                if not isinstance(item, dict) or not isinstance(item.get("stage"), str) or item["stage"] not in ALLOWED_STAGES:
                    continue
                name = item["stage"]
                row = stages[name]
                seconds = item.get("seconds")
                if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds) and seconds >= 0:
                    row["seconds"].append(float(seconds))
                status = item.get("outcome")
                row["outcome"][status if status in {"ok", "error", "cache", "fallback", "timeout", "partial", "unknown"} else "unknown"] += 1
    return routes, stages, calls


def _format_distribution(values):
    if not values:
        return "n=0"
    return "n={} p50={:.4f}s p95={:.4f}s".format(len(values), percentile(values, .50), percentile(values, .95))


class Command(BaseCommand):
    help = "Read a bounded privacy-safe performance JSONL log and report route/stage p50 and p95."

    def add_arguments(self, parser):
        parser.add_argument("log_file", help="Structured performance JSONL file")
        parser.add_argument("--since-hours", type=float, default=24, help="UTC observation window; bounded log tail may cover less")

    def handle(self, *args, **options):
        hours = options["since_hours"]
        if not math.isfinite(hours) or hours <= 0:
            raise CommandError("--since-hours must be positive and finite")
        cutoff = time.time() - hours * 3600
        events = [event for event in read_events(options["log_file"])
                  if isinstance(event.get("at"), (int, float)) and event["at"] >= cutoff]
        self.stdout.write("Observed samples in last {} hours (bounded {} MiB / {} lines; not unique users)".format(hours, MAX_INPUT_BYTES // 1048576, MAX_LINES))
        routes, stages, calls = summarize(events)
        self.stdout.write("Routes")
        for route in sorted(routes):
            row = routes[route]
            cache = " ".join("{}={}".format(key, row["cache"].get(key, 0)) for key in ("hit", "fresh", "stale", "unknown"))
            outcomes = " ".join("{}={}".format(key, count) for key, count in sorted(row["outcome"].items()))
            self.stdout.write("{} {} cache[{}] outcomes[{}]".format(route, _format_distribution(row["seconds"]), cache, outcomes))
            for state, values in sorted(row["cache_seconds"].items()):
                self.stdout.write("  {} {}".format(state, _format_distribution(values)))
        self.stdout.write("Scout stages")
        for stage in sorted(stages):
            row = stages[stage]
            outcomes = " ".join("{}={}".format(key, count) for key, count in sorted(row["outcome"].items()))
            self.stdout.write("{} {} outcomes[{}]".format(stage, _format_distribution(row["seconds"]), outcomes))
        if calls:
            self.stdout.write("Scout calls n={} p50={:.1f} p95={:.1f} (budget=4)".format(len(calls), percentile(calls, .50), percentile(calls, .95)))
