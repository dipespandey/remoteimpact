"""Privacy-safe, bounded performance event helpers.

Events contain only fixed route/stage labels, timings, status classes and counts.
Request data is deliberately never accepted by this module.
"""
import json
import logging
import os
import re
import math
import time

from django.conf import settings

logger = logging.getLogger("performance")


from jobboard.performance_logging import ConcurrentJSONLHandler, PerformanceJSONFormatter


_ALLOWED_OUTCOMES = {"ok", "error", "cache", "fallback", "timeout", "partial", "unknown"}
_ALLOWED_STAGES = {
    "workflow", "parse", "match", "retrieval", "embedding", "plan", "refinement", "reranker",
    "close_matches", "source_profile", "remote_policy", "model", "plan_cache", "verify",
}
_ROUTE_CHARS = re.compile(r"[^a-zA-Z0-9_:/{}.-]")
_ROUTE_CONVERTER = re.compile(r"<(?:(?:str|int|slug|uuid|path):)?([a-zA-Z_][a-zA-Z0-9_]*)>")


def safe_route(route):
    """Return a bounded URL pattern label, never a resolved path or URL."""
    if not isinstance(route, str) or not route or "?" in route or "#" in route:
        return "unknown"
    route = _ROUTE_CONVERTER.sub(r"{\1}", route)
    route = _ROUTE_CHARS.sub("", route)[:120].strip("/")
    return route or "root"


def emit_event(event):
    """Emit a whitelist-shaped event to the dedicated application logger."""
    if not getattr(settings, "PERFORMANCE_METRICS_ENABLED", True):
        return
    if not isinstance(event, dict):
        return
    # Keep the schema deliberately small and numeric/text-only.
    clean = {}
    for key in ("kind", "route", "cache", "outcome", "status"):
        value = event.get(key)
        if isinstance(value, str):
            clean[key] = value[:120]
    for key in ("seconds", "calls", "call_budget", "sample_rate", "cache_hits", "fresh_stages"):
        value = event.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            clean[key] = value
    stages = event.get("stages")
    if isinstance(stages, list):
        clean["stages"] = []
        for stage in stages[:24]:
            if not isinstance(stage, dict) or not isinstance(stage.get("stage"), str) or stage["stage"] not in _ALLOWED_STAGES:
                continue
            item = {"stage": stage["stage"]}
            outcome = stage.get("outcome")
            if isinstance(outcome, str) and outcome in _ALLOWED_OUTCOMES:
                item["outcome"] = outcome
            seconds = stage.get("seconds")
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds):
                item["seconds"] = seconds
            clean["stages"].append(item)
    if clean:
        clean["at"] = round(time.time(), 3)
        if clean.get("kind") == "request":
            clean["route"] = safe_route(clean.get("route"))
        logger.info("performance", extra={"performance": clean})


def scout_event(diagnostics, *, seconds, calls, call_budget=4):
    """Build a Scout aggregate from numeric and fixed-vocabulary diagnostics only."""
    raw_stages = diagnostics.get("stages", []) if isinstance(diagnostics, dict) else []
    stages = []
    cache_counts = {"hit": 0, "fresh": 0}
    outcome = "ok"
    for row in raw_stages[:24] if isinstance(raw_stages, list) else []:
        if not isinstance(row, dict):
            continue
        name = row.get("stage")
        if not isinstance(name, str) or name not in _ALLOWED_STAGES:
            continue
        status = row.get("status")
        normalized = status if isinstance(status, str) and status in _ALLOWED_OUTCOMES else "unknown"
        if normalized == "cache":
            cache_counts["hit"] += 1
        elif normalized in {"ok", "fresh"}:
            cache_counts["fresh"] += 1
        seconds_value = row.get("seconds")
        if isinstance(seconds_value, (int, float)) and not isinstance(seconds_value, bool) and math.isfinite(seconds_value):
            stages.append({"stage": name, "outcome": normalized, "seconds": max(0, float(seconds_value))})
        else:
            stages.append({"stage": name, "outcome": normalized})
        if normalized in {"error", "timeout"}:
            outcome = "partial"
    retrieval_seconds = diagnostics.get("retrieval_seconds") if isinstance(diagnostics, dict) else None
    if isinstance(retrieval_seconds, (int, float)) and not isinstance(retrieval_seconds, bool) and math.isfinite(retrieval_seconds):
        retrieval_outcome = "cache" if diagnostics.get("retrieval_cache") == "hit" else "ok"
        stages.append({"stage": "retrieval", "outcome": retrieval_outcome,
                       "seconds": max(0, float(retrieval_seconds))})
    embedding_seconds = diagnostics.get("query_embedding_seconds") if isinstance(diagnostics, dict) else None
    if isinstance(embedding_seconds, (int, float)) and not isinstance(embedding_seconds, bool) and math.isfinite(embedding_seconds):
        stages.append({"stage": "embedding", "outcome": "ok",
                       "seconds": max(0, float(embedding_seconds))})
    return {
        "kind": "scout",
        "seconds": max(0, float(seconds)),
        "calls": max(0, int(calls)),
        "call_budget": max(0, int(call_budget)),
        "cache": "hit" if cache_counts["hit"] else "fresh",
        "cache_hits": cache_counts["hit"],
        "fresh_stages": cache_counts["fresh"],
        "outcome": outcome,
        "stages": stages,
    }
