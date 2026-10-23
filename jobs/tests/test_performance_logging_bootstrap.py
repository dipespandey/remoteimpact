"""Prove production logging config imports before app registry and writes events."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from django.test import SimpleTestCase
from django.conf import settings


class PerformanceLoggingBootstrapTests(SimpleTestCase):
    def test_handler_module_imports_without_importing_django(self):
        code = (
            "import sys; from jobboard.performance_logging import ConcurrentJSONLHandler; "
            "assert not any(name == 'django' or name.startswith('django.') for name in sys.modules)"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=settings.BASE_DIR,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_settings_start_and_emit_to_configured_file(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "performance.jsonl"
            env = os.environ.copy()
            env.update({
                "DEBUG": "False",
                "SECRET_KEY": "performance-logging-smoke-test-only-key-2026-unique-value",
                "PERFORMANCE_METRICS_ENABLED": "True",
                "PERFORMANCE_LOG_PATH": str(log_path),
                "EMAIL_PROVIDER": "none",
                "DATABASE_URL": "",
            })
            check = subprocess.run(
                [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
                cwd=settings.BASE_DIR, env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

            emit_code = (
                "import django; django.setup(); "
                "from jobs.services.performance import emit_event; "
                "emit_event({'kind':'request','route':'/jobs/?email=private@example.invalid',"
                "'seconds':0.012,'email':'private@example.invalid','prompt':'private prompt',"
                "'user_id':'private-user','query':'private query'})"
            )
            emitted = subprocess.run([sys.executable, "-c", emit_code], cwd=settings.BASE_DIR,
                                     env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(emitted.returncode, 0, emitted.stdout + emitted.stderr)
            rows = [json.loads(line) for line in log_path.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "request")
            self.assertEqual(rows[0]["route"], "unknown")
            self.assertEqual(rows[0]["seconds"], 0.012)
            self.assertIsInstance(rows[0]["at"], (int, float))
            self.assertNotIn("private", log_path.read_text())
            self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o600)
