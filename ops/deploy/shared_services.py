"""Provision bounded private caching and persistent, permission-restricted logs."""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

REDIS_IMAGE = 'redis:7.4-bookworm'  # Resolved to an immutable digest on first provision.


def _write_private(path, value):
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            descriptor = None
            handle.write(value)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def ensure(root):
    root = Path(root)
    private = root / 'secrets'
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o700)
    for path in (root / 'cache', root / 'performance-logs', root / 'redis-data'):
        path.mkdir(mode=0o700, exist_ok=True)
        path.chmod(0o700)
    password_file = private / 'cache-password'
    if not password_file.exists():
        descriptor = os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(secrets.token_hex(32))
    password_file.chmod(0o600)
    password = password_file.read_text().strip()
    if not password or not all(c in '0123456789abcdef' for c in password):
        raise RuntimeError('Invalid private cache credential')
    config = private / 'redis.conf'
    _write_private(config, 'bind 0.0.0.0\nprotected-mode yes\nrequirepass ' + password + '\n'
        'maxmemory 256mb\nmaxmemory-policy allkeys-lru\nappendonly yes\nappendfsync everysec\n'
        'auto-aof-rewrite-percentage 100\nauto-aof-rewrite-min-size 16mb\nsave ""\ndir /data\n')
    image_file = private / 'redis-image'
    if not image_file.exists():
        subprocess.run(['docker', 'pull', REDIS_IMAGE], check=True)
        digest = json.loads(subprocess.check_output(['docker', 'image', 'inspect', REDIS_IMAGE], text=True))[0]['RepoDigests'][0]
        image_file.write_text(digest)
        image_file.chmod(0o600)
    image = image_file.read_text().strip()
    if not re.fullmatch(r'redis@sha256:[0-9a-f]{64}', image):
        raise RuntimeError('Cache image must be pinned by digest')
    exists = subprocess.run(['docker', 'inspect', 'ri-cache'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not exists:
        subprocess.run(['docker', 'run', '-d', '--name', 'ri-cache', '--restart', 'unless-stopped',
            '--network', 'easyclaw_default', '--memory', '512m', '--cpus', '0.5', '--user', '0:0',
            '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2',
            '-v', str(config) + ':/usr/local/etc/redis/redis.conf:ro',
            '-v', str(root / 'redis-data') + ':/data', image, 'redis-server', '/usr/local/etc/redis/redis.conf'], check=True)
    else:
        subprocess.run(['docker', 'start', 'ri-cache'], check=True, stdout=subprocess.DEVNULL)
    env = private / 'performance.env'
    _write_private(env, 'REDIS_URL=redis://:' + password + '@ri-cache:6379/0\n'
        'CACHE_DIR=/var/cache/remoteimpact\nSCOUT_PREVIEW_ASYNC_ENABLED=True\n'
        'SCOUT_PREVIEW_ACTIVE_STATE_FILE=/run/remoteimpact-releases/state.json\n'
        'PERFORMANCE_METRICS_ENABLED=True\nPERFORMANCE_LOG_PATH=/var/log/remoteimpact/performance.jsonl\n')
    return env


if __name__ == '__main__':
    import sys
    ensure(sys.argv[1])
