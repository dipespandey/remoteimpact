import importlib.util
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

MODULE_PATH = Path(__file__).with_name('release_supervisor.py')
spec = importlib.util.spec_from_file_location('release_supervisor', MODULE_PATH)
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


def supervisor_process(commands, grace=2):
    """Start supervise in a child process so its signal handlers are isolated."""
    module = repr(str(MODULE_PATH))
    code = (
        'import importlib.util,sys; '
        f's=importlib.util.spec_from_file_location("release_supervisor",{module}); '
        'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
        f'sys.exit(m.supervise({commands!r},grace={grace}))'
    )
    return subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for(path, process, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return
        if process.poll() is not None:
            raise AssertionError(f'supervisor exited early with {process.returncode}')
        time.sleep(0.03)
    raise AssertionError(f'child did not create {path}')


class ReleaseSupervisorTests(unittest.TestCase):
    def test_child_failure_drains_sibling_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            stopped = Path(folder) / 'sibling-stopped'
            sibling = (
                'import pathlib,signal,time,sys; '
                f'p=pathlib.Path({str(stopped)!r}); '
                'signal.signal(signal.SIGTERM,lambda *_:(p.write_text("term"),sys.exit(0))); '
                'time.sleep(30)'
            )
            failure = 'import time; time.sleep(0.3); raise SystemExit(7)'
            result = supervisor.supervise([
                [sys.executable, '-c', failure],
                [sys.executable, '-c', sibling],
            ], grace=2)
            self.assertEqual(result, 7)
            self.assertEqual(stopped.read_text(), 'term')

    def test_supervisor_ignores_hup_and_drains_child_on_sigterm(self):
        with tempfile.TemporaryDirectory() as folder:
            ready, stopped = Path(folder) / 'ready', Path(folder) / 'stopped'
            child = (
                'import pathlib,signal,time,sys; '
                f'r=pathlib.Path({str(ready)!r}); s=pathlib.Path({str(stopped)!r}); '
                'signal.signal(signal.SIGTERM,lambda *_:(s.write_text("term"),sys.exit(0))); '
                'r.write_text("ready"); time.sleep(30)'
            )
            process = supervisor_process([[sys.executable, '-c', child]])
            try:
                wait_for(ready, process)
                process.send_signal(signal.SIGHUP)
                time.sleep(0.2)
                self.assertIsNone(process.poll(), 'SIGHUP must not terminate the immutable release supervisor')
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertEqual(stopped.read_text(), 'term')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)


if __name__ == '__main__':
    unittest.main()
