import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name('shared_services.py')
spec = importlib.util.spec_from_file_location('shared_services', MODULE_PATH)
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)

blue_spec = importlib.util.spec_from_file_location('blue_green', Path(__file__).with_name('blue_green.py'))
blue_green = importlib.util.module_from_spec(blue_spec)
blue_spec.loader.exec_module(blue_green)


class SharedServiceProvisioningTests(unittest.TestCase):
    def test_check_only_does_not_provision_shared_services(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            release = blue_green.Release(root)
            source = root / 'repo'
            (source / 'ops/deploy').mkdir(parents=True)
            for file in ('pyproject.toml', 'uv.lock', '.dockerignore',
                         'ops/deploy/entrypoint-release.sh', 'ops/deploy/shared_services.py'):
                (source / file).write_text('fixture')
            (root / 'Dockerfile.prod').write_text('fixture')
            release.save({'active': {'id': 'old', 'port': 8100, 'release': 'old'}, 'previous': None})
            calls = []

            def run(*args, **kwargs):
                calls.append(args)
                if args[0] == 'rsync':
                    shutil.copytree(source, args[-1], dirs_exist_ok=True)
                if args[:2] == ('docker', 'top'):
                    return 'PID\n1\n2\n'
                if args[:2] == ('docker', 'create'):
                    return 'candidate'
                return ''

            with patch.object(blue_green, 'run', side_effect=run), \
                 patch.object(release, 'ready'), patch.object(release, 'switch'):
                release.deploy(check_only=True)

            self.assertFalse(any(call and call[0] == sys.executable and
                                 any(str(part).endswith('/ops/deploy/shared_services.py') for part in call)
                                 for call in calls))
            self.assertFalse((root / 'secrets').exists())
            self.assertFalse((root / 'cache').exists())
            self.assertFalse((root / 'redis-data').exists())

    def test_provisioning_pins_bounded_private_redis_without_printing_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []

            def run(args, **kwargs):
                calls.append(args)
                status = 1 if args[:3] == ['docker', 'inspect', 'ri-cache'] else 0
                return shared.subprocess.CompletedProcess(args, status)

            digest = 'a' * 64
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(shared.subprocess, 'run', side_effect=run), \
                 patch.object(shared.subprocess, 'check_output', return_value=json.dumps([
                     {'RepoDigests': [f'redis@sha256:{digest}']}
                 ])), redirect_stdout(stdout), redirect_stderr(stderr):
                env_path = shared.ensure(folder)

            password = (Path(folder) / 'secrets/cache-password').read_text().strip()
            config = (Path(folder) / 'secrets/redis.conf').read_text()
            env = env_path.read_text()
            container = next(call for call in calls if call[:3] == ['docker', 'run', '-d'])
            self.assertEqual(env_path, Path(folder) / 'secrets/performance.env')
            self.assertIn(f'requirepass {password}\n', config)
            self.assertIn('maxmemory 256mb\n', config)
            self.assertIn('maxmemory-policy allkeys-lru\n', config)
            self.assertIn('appendonly yes\n', config)
            self.assertIn(password, env)
            self.assertIn(f'redis@sha256:{digest}', container)
            self.assertEqual(container[container.index('--memory') + 1], '512m')
            self.assertEqual(container[container.index('--cpus') + 1], '0.5')
            self.assertIn('--network', container)
            self.assertNotIn('-p', container)
            self.assertEqual((Path(folder) / 'secrets').stat().st_mode & 0o777, 0o700)
            self.assertEqual((Path(folder) / 'secrets/cache-password').stat().st_mode & 0o777, 0o600)
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(password, stdout.getvalue())
            self.assertNotIn(password, stderr.getvalue())

    def test_unpinned_existing_image_file_is_rejected_before_container_start(self):
        with tempfile.TemporaryDirectory() as folder:
            secrets = Path(folder) / 'secrets'
            secrets.mkdir(mode=0o700)
            (secrets / 'cache-password').write_text('a' * 64)
            (secrets / 'cache-password').chmod(0o644)
            (secrets / 'redis-image').write_text('redis@sha256:' + 'z' * 64)
            secrets.chmod(0o755)
            calls = []

            def run(args, **kwargs):
                calls.append(args)
                return shared.subprocess.CompletedProcess(args, 1 if args[:3] == ['docker', 'inspect', 'ri-cache'] else 0)

            with patch.object(shared.subprocess, 'run', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'pinned by digest'):
                    shared.ensure(folder)
            self.assertFalse(any(call[:2] == ['docker', 'run'] for call in calls))
            self.assertEqual(secrets.stat().st_mode & 0o777, 0o700)
            self.assertEqual((secrets / 'cache-password').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
