from importlib.machinery import SourceFileLoader
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

module = SourceFileLoader('exec_active', str(Path(__file__).with_name('exec-active'))).load_module()

class ExecActiveTests(unittest.TestCase):
    def test_cutover_job_completion_requests_cleanup_and_preserves_exit_code(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / 'state.json'
            state.write_text(json.dumps({'active': {'id': 'old'}}))
            def invoke(args, **kwargs):
                if args[:2] == ['docker', 'exec']:
                    state.write_text(json.dumps({'active': {'id': 'new'}}))
                    return SimpleNamespace(returncode=7)
                return SimpleNamespace(returncode=1)
            with patch.object(module.subprocess, 'run', side_effect=invoke) as run:
                self.assertEqual(module.execute(['python', 'manage.py', 'check'], root), 7)
            self.assertEqual(run.call_args.args[0][-1], 'stop-previous')

    def test_current_release_job_does_not_request_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'state.json').write_text(json.dumps({'active': {'id': 'current'}}))
            with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(module.execute(['true'], root), 0)
            run.assert_called_once_with(['docker', 'exec', 'current', 'true'])
