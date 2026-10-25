import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from ops.security.ensure_active import ensure


class RecordedReleaseRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'releases'
        self.state.mkdir()
        (self.state / 'state.json').write_text(json.dumps({
            'active': {'id': 'a' * 64, 'port': 8101}
        }))

    @patch('ops.security.ensure_active.subprocess.check_output')
    def test_interrupted_cutover_is_never_guessed(self, inspect):
        (self.state / 'pending.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'operator review'):
            ensure(self.root)
        inspect.assert_not_called()

    @patch('ops.security.ensure_active.subprocess.check_output')
    def test_invalid_release_is_rejected_before_docker(self, inspect):
        (self.state / 'state.json').write_text(json.dumps({'active': {'id': 'not-an-id', 'port': 8000}}))
        with self.assertRaisesRegex(RuntimeError, 'Invalid'):
            ensure(self.root)
        inspect.assert_not_called()

    @patch('ops.security.ensure_active.urllib.request.urlopen')
    @patch('ops.security.ensure_active.subprocess.run')
    @patch('ops.security.ensure_active.subprocess.check_output', side_effect=['true', 'true', 'false'])
    def test_starts_only_stopped_recorded_release_and_checks_health(self, inspect, run, open_url):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        open_url.return_value = response
        ensure(self.root)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ['docker', 'start', 'a' * 64])
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:8101/healthz/')
