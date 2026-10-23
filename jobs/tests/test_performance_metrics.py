import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from jobs.management.commands.perf_report import read_events, summarize
from jobs.middleware import PerformanceMiddleware
from jobs.services.performance import ConcurrentJSONLHandler, PerformanceJSONFormatter, safe_route, scout_event


class PerformanceMetricsTests(SimpleTestCase):
    def test_route_label_uses_pattern_without_values(self):
        self.assertEqual(safe_route("jobs/<slug:slug>/"), "jobs/{slug}")
        self.assertEqual(safe_route("/jobs/user@example.com?q=secret"), "unknown")

    def test_scout_event_keeps_only_bounded_performance_fields(self):
        event = scout_event({"stages": [
            {"stage": "parse", "status": "cache", "seconds": .01, "intent": "private"},
            {"stage": "match", "status": "ok", "seconds": .23},
            {"stage": "unknown", "status": "error", "seconds": 999},
        ]}, seconds=.5, calls=2)
        self.assertEqual(event["cache_hits"], 1)
        self.assertEqual(event["fresh_stages"], 1)
        self.assertEqual(event["calls"], 2)
        self.assertEqual([stage["stage"] for stage in event["stages"]], ["parse", "match"])
        self.assertNotIn("intent", json.dumps(event))

    def test_report_uses_observed_durations_and_groups_route_and_stage(self):
        events = [
            {"kind": "request", "route": "jobs/", "seconds": .1, "cache": "hit", "outcome": "ok"},
            {"kind": "request", "route": "jobs/", "seconds": .2, "cache": "fresh", "outcome": "ok"},
            {"kind": "request", "route": "jobs/", "seconds": .3, "cache": "fresh", "outcome": "error"},
            {"kind": "scout", "seconds": .4, "calls": 2, "stages": [
                {"stage": "parse", "seconds": .05, "outcome": "cache"},
                {"stage": "match", "seconds": .35, "outcome": "ok"},
            ]},
        ]
        routes, stages, calls = summarize(events)
        self.assertEqual(routes["jobs/"]["seconds"], [.1, .2, .3])
        self.assertEqual(routes["jobs/"]["cache"]["hit"], 1)
        self.assertEqual(routes["jobs/"]["cache"]["fresh"], 2)
        self.assertEqual(stages["parse"]["seconds"], [.05])
        self.assertEqual(stages["match"]["seconds"], [.35])
        self.assertEqual(calls, [2])

    def test_report_reads_only_a_bounded_jsonl_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "performance.jsonl"
            path.write_text('{"kind":"request","seconds":0.25}\nnot-json\n')
            rows = list(read_events(path))
        self.assertEqual(rows, [{"kind": "request", "seconds": 0.25}])

    @override_settings(PERFORMANCE_METRICS_ENABLED=True, PERFORMANCE_SAMPLE_RATE=1.0)
    def test_middleware_emits_route_template_and_server_timing(self):
        request = RequestFactory().get("/jobs/private-slug/?q=secret")
        request.resolver_match = SimpleNamespace(route="jobs/<slug:slug>/")
        request._performance_cache_status = "hit"
        middleware = PerformanceMiddleware(lambda req: HttpResponse("ok"))
        middleware.process_request(request)
        with patch("jobs.middleware.emit_event") as emit:
            response = middleware.process_response(request, HttpResponse("ok"))
        self.assertIn("app;dur=", response["Server-Timing"])
        event = emit.call_args.args[0]
        self.assertEqual(event["route"], "jobs/{slug}")
        self.assertEqual(event["cache"], "hit")
        self.assertNotIn("private-slug", json.dumps(event))
        self.assertNotIn("secret", json.dumps(event))

    @override_settings(PUBLIC_PAGE_CACHE_ENABLED=True, CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "performance-test"}})
    def test_public_page_cache_marker_distinguishes_fresh_and_hit(self):
        from django.contrib.auth.models import AnonymousUser
        from django.core.cache import cache
        from jobs.services.page_cache import cache_public_page
        cache.clear()
        calls = []
        @cache_public_page(60)
        def view(request):
            calls.append(True)
            return HttpResponse("body")
        first = RequestFactory().get("/cached/")
        first.user = AnonymousUser()
        first.resolver_match = SimpleNamespace(route="cached/")
        view(first)
        second = RequestFactory().get("/cached/")
        second.user = AnonymousUser()
        second.resolver_match = SimpleNamespace(route="cached/")
        view(second)
        self.assertEqual(first._performance_cache_status, "fresh")
        self.assertEqual(second._performance_cache_status, "hit")
        self.assertEqual(len(calls), 1)

    def test_concurrent_jsonl_handler_rotates_with_private_permissions(self):
        import logging
        import os
        import stat
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            handler = ConcurrentJSONLHandler(path, max_bytes=1024, backup_count=2)
            record = logging.LogRecord("performance", logging.INFO, "x.py", 1, "msg", (), None)
            record.performance = {"kind": "request", "route": "safe/", "seconds": 0.1, "padding": "x" * 700}
            for _ in range(20):
                handler.emit(record)
            self.assertTrue(Path(str(path) + ".1").exists())
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(str(path) + ".lock").st_mode), 0o600)
            self.assertIn(b'"route":"safe/"', path.read_bytes())
            handler.close()

    def test_bounded_workflow_emits_numeric_summary_without_diagnostics(self):
        from jobs.services.scout_workflow import bounded_workflow, record_stage
        diagnostics = {"private_intent": "confidential"}
        with patch("jobs.services.performance.emit_event") as emit:
            with bounded_workflow(diagnostics):
                record_stage("match", "ok", seconds=0.01, private="value")
        event = emit.call_args.args[0]
        self.assertEqual(event["kind"], "scout")
        self.assertEqual(event["stages"][0]["stage"], "match")
        self.assertNotIn("private", json.dumps(event))
        self.assertNotIn("confidential", json.dumps(event))

    def test_json_formatter_serializes_only_performance_payload(self):
        import logging
        record = logging.LogRecord("performance", logging.INFO, "x.py", 1, "msg", (), None)
        record.performance = {"kind": "request", "route": "jobs/", "seconds": .12}
        self.assertEqual(json.loads(PerformanceJSONFormatter().format(record)), record.performance)
