"""Provision bounded private caching and persistent, permission-restricted logs."""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

REDIS_IMAGE = 'redis:7.4-bookworm'  # Resolved to an immutable digest on first provision.
REDIS_CONFIG_PATH = '/usr/local/etc/redis/redis.conf'


def _redis_identity(image):
    output = subprocess.check_output(
        ['docker', 'run', '--rm', '--entrypoint', 'sh', image,
         '-c', 'id -u redis; id -g redis'], text=True,
    )
    try:
        uid, gid = (int(line) for line in output.splitlines())
    except (TypeError, ValueError):
        raise RuntimeError('Unable to determine Redis image user') from None
    if uid < 1 or gid < 1:
        raise RuntimeError('Redis image user must be unprivileged')
    return uid, gid


def _validate_existing(info, *, network, image, config, data):
    config = str(config.resolve())
    data = str(data.resolve())
    if info.get('Config', {}).get('Image') != image:
        raise RuntimeError('Existing cache container image does not match the pinned digest')
    host = info.get('HostConfig', {})
    mounts = info.get('Mounts', [])
    if host.get('Memory') != 512 * 1024 * 1024 or host.get('NanoCpus') != 500_000_000:
        raise RuntimeError('Existing cache container resource limits are unsafe')
    if host.get('PortBindings'):
        raise RuntimeError('Existing cache container publishes a host port')
    targets = {(m.get('Source'), m.get('Destination'), m.get('RW')) for m in mounts}
    if (config, REDIS_CONFIG_PATH, False) not in targets or (data, '/data', True) not in targets:
        raise RuntimeError('Existing cache container mounts do not match private persistent paths')
    if network not in info.get('NetworkSettings', {}).get('Networks', {}):
        raise RuntimeError('Existing cache container is outside the private application network')
    status = info.get('State', {}).get('Status')
    if status not in {'running', 'restarting', 'exited', 'created'}:
        raise RuntimeError('Existing cache container has an unexpected state')
    return status


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


def _wait_for_cache(name, password, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ['docker', 'exec', '-i', name, 'redis-cli', '--raw'],
                input=f'AUTH {password}\nPING\n', text=True,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
            )
            if result.returncode == 0 and result.stdout.strip().splitlines()[-1:] == ['PONG']:
                return
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(0.5)
    raise RuntimeError('Private cache did not become ready')


def ensure(root, *, container_name='ri-cache', network='easyclaw_default'):
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
    uid, gid = _redis_identity(image)
    os.chown(config, uid, gid)
    os.chmod(config, 0o600)
    inspection = subprocess.run(['docker', 'inspect', container_name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    exists = inspection.returncode == 0
    if not exists:
        subprocess.run(['docker', 'run', '-d', '--name', container_name, '--restart', 'unless-stopped',
            '--network', network, '--memory', '512m', '--cpus', '0.5', '--user', '0:0',
            '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2',
            '-v', str(config) + ':' + REDIS_CONFIG_PATH + ':ro',
            '-v', str(root / 'redis-data') + ':/data', image, 'redis-server', '/usr/local/etc/redis/redis.conf'], check=True)
    else:
        try:
            info = json.loads(inspection.stdout)[0]
        except (IndexError, ValueError, TypeError):
            raise RuntimeError('Unable to verify existing cache container') from None
        status = _validate_existing(info, network=network, image=image,
                                    config=config, data=root / 'redis-data')
        if status == 'restarting':
            # The previously mounted config may have been unreadable by the
            # image's redis user. Keep its verified data volume and restart
            # with the corrected owner/mode above.
            subprocess.run(['docker', 'stop', '--time', '10', container_name], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['docker', 'start', container_name], check=True, stdout=subprocess.DEVNULL)
        elif status in {'exited', 'created'}:
            subprocess.run(['docker', 'start', container_name], check=True, stdout=subprocess.DEVNULL)
    _wait_for_cache(container_name, password)
    env = private / 'performance.env'
    _write_private(env, 'REDIS_URL=redis://:' + password + '@' + container_name + ':6379/0\n'
        'CACHE_DIR=/var/cache/remoteimpact\nSCOUT_PREVIEW_ASYNC_ENABLED=True\n'
        'SCOUT_PREVIEW_ACTIVE_STATE_FILE=/run/remoteimpact-releases/state.json\n'
        'PERFORMANCE_METRICS_ENABLED=True\nPERFORMANCE_LOG_PATH=/var/log/remoteimpact/performance.jsonl\n')
    return env


if __name__ == '__main__':
    import sys
    ensure(sys.argv[1])
