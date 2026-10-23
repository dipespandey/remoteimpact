"""Logical Redis database isolation and an opt-in real Docker provisioning smoke."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from urllib.parse import parse_qsl, urlsplit

from jobboard.cache_urls import redis_url_for_database

MODULE_PATH = Path(__file__).with_name('shared_services.py')
spec = importlib.util.spec_from_file_location('shared_services', MODULE_PATH)
shared_services = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared_services)


class RedisCacheURLTests(unittest.TestCase):
    def test_tcp_urls_keep_auth_and_options_but_force_selected_database(self):
        original = 'rediss://cache-user:p%40ss@redis.example:6380/8?socket_timeout=1&db=9&ssl_cert_reqs=required'
        for db in (0, 1, 2):
            with self.subTest(db=db):
                result = urlsplit(redis_url_for_database(original, db))
                self.assertEqual((result.scheme, result.netloc, result.path),
                                 ('rediss', 'cache-user:p%40ss@redis.example:6380', f'/{db}'))
                self.assertEqual(dict(parse_qsl(result.query)),
                                 {'socket_timeout': '1', 'ssl_cert_reqs': 'required'})

    def test_unix_url_replaces_db_query_without_losing_socket_or_auth(self):
        result = urlsplit(redis_url_for_database('unix:///run/redis.sock?db=9&password=secret', 1))
        self.assertEqual(result.path, '/run/redis.sock')
        self.assertEqual(dict(parse_qsl(result.query)), {'password': 'secret', 'db': '1'})


@unittest.skipUnless(
    os.environ.get('RUN_REDIS_DOCKER_SMOKE') == '1' and shutil.which('docker'),
    'set RUN_REDIS_DOCKER_SMOKE=1 to run the isolated Docker/Redis integration smoke',
)
class RealRedisCacheSmokeTests(unittest.TestCase):
    def test_provisioning_auth_aof_and_django_clear_are_isolated(self):
        token = uuid.uuid4().hex[:12]
        container = 'ri-cache-ci-' + token
        network = 'ri-cache-ci-net-' + token
        with tempfile.TemporaryDirectory(prefix='ri-redis-ci-') as folder:
            root = Path(folder)
            subprocess.run(['docker', 'network', 'create', network], check=True,
                           stdout=subprocess.DEVNULL)
            try:
                env_path = shared_services.ensure(root, container_name=container, network=network)
                password = (root / 'secrets/cache-password').read_text().strip()
                inspection = json.loads(subprocess.check_output(
                    ['docker', 'inspect', container], text=True))[0]
                host = inspection['HostConfig']
                uid, gid = shared_services._redis_identity(inspection['Config']['Image'])
                self.assertEqual(inspection['State']['Status'], 'running')
                self.assertFalse(host.get('PortBindings'))
                self.assertEqual(host['Memory'], 512 * 1024 * 1024)
                self.assertEqual(host['NanoCpus'], 500_000_000)
                self.assertEqual((root / 'secrets/redis.conf').stat().st_uid, uid)
                self.assertEqual((root / 'secrets/redis.conf').stat().st_gid, gid)
                self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)

                def redis_cli(commands):
                    return subprocess.run(
                        ['docker', 'exec', '-i', container, 'redis-cli', '--raw'],
                        input=f'AUTH {password}\n{commands}\n', text=True,
                        capture_output=True, timeout=10, check=True,
                    ).stdout

                self.assertIn('PONG', redis_cli('PING'))
                self.assertIn('yes', redis_cli('CONFIG GET appendonly'))
                self.assertIn('268435456', redis_cli('CONFIG GET maxmemory'))
                self.assertIn('allkeys-lru', redis_cli('CONFIG GET maxmemory-policy'))
                redis_cli('SET integration-aof-check persistent')
                aof_dir = root / 'redis-data/appendonlydir'
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not aof_dir.exists():
                    time.sleep(0.05)
                self.assertTrue(aof_dir.exists(), 'Redis AOF files must persist in the mounted data directory')

                identity_before = inspection['Id']
                subprocess.run(['docker', 'stop', '--time', '10', container], check=True,
                               stdout=subprocess.DEVNULL)
                shared_services.ensure(root, container_name=container, network=network)
                recovered = json.loads(subprocess.check_output(
                    ['docker', 'inspect', container], text=True))[0]
                self.assertEqual(recovered['Id'], identity_before)
                self.assertEqual(recovered['State']['Status'], 'running')
                self.assertIn('persistent', redis_cli('GET integration-aof-check'))

                address = subprocess.check_output([
                    'docker', 'inspect', '--format',
                    '{{(index .NetworkSettings.Networks "' + network + '").IPAddress}}',
                    container,
                ], text=True).strip()
                django_script = """
import django
django.setup()
from django.conf import settings
from django.core.cache import caches
assert settings.CACHES['default']['LOCATION'].endswith('/0')
assert settings.CACHES['job_extraction']['LOCATION'].endswith('/1')
assert settings.CACHES['scout_decisions']['LOCATION'].endswith('/2')
caches['default'].set('integration-default', 'page', 120)
caches['job_extraction'].set('integration-extraction', 'facts', 120)
caches['scout_decisions'].set('integration-scout', 'decision', 120)
caches['default'].clear()
assert caches['default'].get('integration-default') is None
assert caches['job_extraction'].get('integration-extraction') == 'facts'
assert caches['scout_decisions'].get('integration-scout') == 'decision'
"""
                child_env = os.environ.copy()
                child_env.update({
                    'REDIS_URL': f'redis://:{password}@{address}:6379/0',
                    'SECRET_KEY': 'redis-isolation-ci-key',
                    'DEBUG': 'True',
                    'PERFORMANCE_LOG_PATH': '',
                    'DJANGO_SETTINGS_MODULE': 'jobboard.settings',
                })
                subprocess.run([sys.executable, '-c', django_script], env=child_env,
                               cwd=Path(__file__).resolve().parents[2], check=True,
                               capture_output=True, text=True, timeout=30)
            finally:
                subprocess.run(['docker', 'rm', '-f', container], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(['docker', 'network', 'rm', network], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    unittest.main()
