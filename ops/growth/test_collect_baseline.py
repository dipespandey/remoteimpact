from datetime import date
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('organic_baseline', Path(__file__).with_name('collect_baseline.py'))
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


class BaselineTests(unittest.TestCase):
    def test_periods_are_adjacent_equal_length_across_month_boundary(self):
        current, previous = baseline.periods(date(2026, 10, 15), 28)
        self.assertEqual(current, ('current', date(2026, 9, 18), date(2026, 10, 15)))
        self.assertEqual(previous, ('previous', date(2026, 8, 21), date(2026, 9, 17)))

    def test_provider_failure_is_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'source.json'
            result = subprocess.CompletedProcess([], 1, '', 'login required')
            with patch.object(baseline.subprocess, 'run', return_value=result):
                report = baseline.run_json(['seo'], target)
            self.assertEqual(report['status'], 'unavailable')
            self.assertNotIn('data', report)
            self.assertFalse(target.exists())
            self.assertEqual(target.with_suffix('.error.txt').read_text(), 'login required')

    def test_invalid_json_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.CompletedProcess([], 0, '<html>service unavailable</html>', '')
            with patch.object(baseline.subprocess, 'run', return_value=result):
                report = baseline.run_json(['seo'], Path(directory) / 'source.json')
            self.assertEqual(report['status'], 'unavailable')

    def test_timeout_preserves_unavailable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(baseline.subprocess, 'run', side_effect=subprocess.TimeoutExpired('seo', 180)):
                report = baseline.run_json(['seo'], Path(directory) / 'source.json')
            self.assertEqual(report['status'], 'unavailable')
