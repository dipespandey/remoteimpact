import importlib.util
import json
import fcntl
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('blue_green', Path(__file__).with_name('blue_green.py'))
bg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bg)

CADDY = ':80 {\n reverse_proxy 127.0.0.1:3456\n}\n\nremoteimpact.org {\n\treverse_proxy 127.0.0.1:8100\n\tlog {\n\t\toutput file /var/log/caddy/remoteimpact-access.log\n\t}\n}\n'

class ReleaseTests(unittest.TestCase):
    def test_only_production_upstream_changes_and_header_is_replaced(self):
        first = bg.caddy_config(CADDY, 8101, 'one')
        second = bg.caddy_config(first, 8102, 'two')
        self.assertIn('reverse_proxy 127.0.0.1:3456', second)
        self.assertIn('reverse_proxy 127.0.0.1:8102', second)
        self.assertIn('output file /var/log/caddy/remoteimpact-access.log', second)
        self.assertEqual(second.count('X-RemoteImpact-Release'), 1)
        self.assertIn('X-RemoteImpact-Release two', second)

    def test_unexpected_proxy_layout_is_rejected(self):
        for value in ('', CADDY + CADDY, CADDY.replace('127.0.0.1:8100', 'app:8000')):
            with self.assertRaises(RuntimeError):
                bg.caddy_config(value, 8101, 'one')

    def test_invalid_caddy_never_changes_live_file(self):
        with tempfile.TemporaryDirectory() as folder:
            release = bg.Release(folder)
            release.caddy = Path(folder) / 'Caddyfile'
            release.caddy.write_text(CADDY)
            with patch.object(bg, 'run', side_effect=RuntimeError('invalid')):
                with self.assertRaises(RuntimeError):
                    release.switch({'port': 8101, 'release': 'one'})
            self.assertEqual(release.caddy.read_text(), CADDY)

    def test_reload_failure_restores_disk_and_reloads_old_config(self):
        with tempfile.TemporaryDirectory() as folder:
            release = bg.Release(folder)
            release.caddy = Path(folder) / 'Caddyfile'
            release.caddy.write_text(CADDY)
            with patch.object(bg, 'run', side_effect=[None, RuntimeError('reload'), None]) as run:
                with self.assertRaises(RuntimeError):
                    release.switch({'port': 8101, 'release': 'one'})
            self.assertEqual(release.caddy.read_text(), CADDY)
            self.assertEqual(run.call_count, 3)

    def test_failed_rollback_probe_never_switches_live_traffic(self):
        with tempfile.TemporaryDirectory() as folder:
            release = bg.Release(folder)
            state = {'active': {'id': 'new', 'port': 8101}, 'previous': {'id': 'old', 'port': 8100}}
            release.save(state)
            with patch.object(bg, 'run'), patch.object(release, 'ready', side_effect=RuntimeError('unhealthy')), patch.object(release, 'switch') as switch:
                with self.assertRaises(RuntimeError):
                    release.rollback()
                switch.assert_not_called()
            self.assertEqual(release.load(), state)

    def pipeline_fixture(self, folder):
        release = bg.Release(folder)
        source = Path(folder) / 'repo'
        (source / 'ops/deploy').mkdir(parents=True)
        for file in ('pyproject.toml', 'uv.lock', '.dockerignore', 'ops/deploy/entrypoint-release.sh'):
            (source / file).write_text('fixture')
        (Path(folder) / 'Dockerfile.prod').write_text('fixture')
        release.save({'active': {'id': 'old', 'port': 8100, 'release': 'old'}, 'previous': None})
        return release, source

    def pipeline_run(self, source, calls, migration_failure=False):
        def fake(*args, **kwargs):
            calls.append(args)
            if args[0] == 'rsync':
                import shutil
                shutil.copytree(source, args[-1], dirs_exist_ok=True)
            if args[:2] == ('docker', 'create'):
                return 'candidate'
            if migration_failure and args[-2:] == ('migrate', '--check'):
                raise bg.subprocess.CalledProcessError(1, args)
            return ''
        return fake

    def test_pending_migration_keeps_live_traffic_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            release, source = self.pipeline_fixture(folder)
            calls = []
            with patch.object(bg, 'run', side_effect=self.pipeline_run(source, calls, True)), patch.object(release, 'ready'), patch.object(release, 'switch') as switch:
                with self.assertRaisesRegex(RuntimeError, 'Pending migrations'):
                    release.deploy()
                switch.assert_not_called()
            self.assertIn(('docker', 'rm', '-f', 'candidate'), calls)
            self.assertFalse(any('migrate' in call and '--noinput' in call for call in calls))
            self.assertEqual(release.load()['active']['id'], 'old')

    def test_failed_post_cutover_check_restores_old_release(self):
        with tempfile.TemporaryDirectory() as folder:
            release, source = self.pipeline_fixture(folder)
            calls = []
            with patch.object(bg, 'run', side_effect=self.pipeline_run(source, calls)), patch.object(release, 'ready', side_effect=[None, None, None, RuntimeError('post check'), None]), patch.object(release, 'switch') as switch:
                with self.assertRaisesRegex(RuntimeError, 'post check'):
                    release.deploy()
                self.assertEqual(switch.call_args_list[-1].args[0]['id'], 'old')
            self.assertEqual(release.load()['active']['id'], 'old')
            self.assertFalse((release.state / 'pending.json').exists())
            self.assertIn(('docker', 'rm', '-f', 'candidate'), calls)

    def test_failed_automatic_rollback_does_not_destroy_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            release, source = self.pipeline_fixture(folder)
            calls = []
            with patch.object(bg, 'run', side_effect=self.pipeline_run(source, calls)), patch.object(release, 'ready', side_effect=[None, None, None, RuntimeError('post check')]), patch.object(release, 'switch', side_effect=[None, RuntimeError('rollback reload failed')]):
                with self.assertRaisesRegex(RuntimeError, 'rollback reload failed'):
                    release.deploy()
            self.assertNotIn(('docker', 'rm', '-f', 'candidate'), calls)
            self.assertTrue((release.state / 'pending.json').exists())

    def test_check_only_never_switches_or_syncs_plans(self):
        with tempfile.TemporaryDirectory() as folder:
            release, source = self.pipeline_fixture(folder)
            calls = []
            with patch.object(bg, 'run', side_effect=self.pipeline_run(source, calls)), patch.object(release, 'ready'), patch.object(release, 'switch') as switch:
                release.deploy(check_only=True)
                switch.assert_not_called()
            self.assertFalse(any('sync_org_plans' in call for call in calls))
            self.assertIn(('docker', 'rm', '-f', 'candidate'), calls)

    def test_running_job_prevents_retiring_its_container(self):
        with tempfile.TemporaryDirectory() as folder:
            release = bg.Release(folder)
            with open(release.state / 'old.jobs.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_SH)
                with patch.object(bg, 'run') as run:
                    with self.assertRaisesRegex(RuntimeError, 'running scheduled job'):
                        release.retire({'id': 'old'})
                    run.assert_not_called()

    def test_check_only_rejects_migration_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            release, source = self.pipeline_fixture(folder)
            with patch.object(release, 'ready'), patch.object(bg, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'never apply migrations'):
                    release.deploy(check_only=True, allow_migrations=True)
                run.assert_not_called()

    def test_uncommitted_host_code_blocks_git_release(self):
        with tempfile.TemporaryDirectory() as folder:
            release, source = self.pipeline_fixture(folder)
            with patch.object(release, 'ready'), patch.object(bg, 'run', return_value=' M jobs/views/jobs.py') as run:
                with self.assertRaisesRegex(RuntimeError, 'uncommitted changes'):
                    release.deploy(ref='a' * 40)
            self.assertFalse(any(call.args[:2] == ('docker', 'create') for call in run.call_args_list))

    def test_state_is_atomic_private_json(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'state.json'
            bg.atomic(target, json.dumps({'active': {'id': 'abc'}}))
            self.assertEqual(json.loads(target.read_text())['active']['id'], 'abc')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertFalse(target.with_name('state.json.tmp').exists())

if __name__ == '__main__':
    unittest.main()
