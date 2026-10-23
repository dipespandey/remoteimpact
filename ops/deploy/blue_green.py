#!/usr/bin/env python3
"""Single-host releases. No source mounts; Caddy cutover only after readiness."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request


def run(*args, capture=False, **kwargs):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE if capture else None, **kwargs).stdout


def atomic(path, data, mode=0o600):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def caddy_config(text, port, release):
    pattern = r'(?ms)^remoteimpact\.org \{.*?^\}'
    blocks = list(re.finditer(pattern, text))
    if len(blocks) != 1:
        raise RuntimeError('Expected exactly one remoteimpact.org Caddy block')
    block = blocks[0].group()
    block, count = re.subn(r'(?m)^\s*reverse_proxy 127\.0\.0\.1:\d+\s*$', f'\treverse_proxy 127.0.0.1:{port}', block)
    if count != 1:
        raise RuntimeError('Expected one local production upstream')
    block = re.sub(r'(?m)^\s*header X-RemoteImpact-Release .*\n', '', block)
    block = block.replace('remoteimpact.org {', f'remoteimpact.org {{\n\theader X-RemoteImpact-Release {release}', 1)
    return text[:blocks[0].start()] + block + text[blocks[0].end():]


class Release:
    def __init__(self, root):
        self.root = Path(root)
        self.state = self.root / 'releases'
        self.state.mkdir(exist_ok=True)
        self.caddy = Path('/etc/caddy/Caddyfile')

    def load(self):
        path = self.state / 'state.json'
        if path.exists():
            return json.loads(path.read_text())
        cid = run('docker', 'inspect', '-f', '{{.Id}}', 'easyclaw-web-1', capture=True).strip()
        return {'active': {'id': cid, 'port': 8100, 'release': 'legacy'}, 'previous': None}

    def save(self, data):
        atomic(self.state / 'state.json', json.dumps(data, indent=2) + '\n')

    def ready(self, target, paths=('/healthz/',), release_header=False):
        for path in paths:
            if release_header:
                # Check the actual local Caddy TLS route, avoiding CDN/cache ambiguity.
                headers = run('curl', '--fail', '--silent', '--show-error', '--max-time', '30', '--resolve',
                              'remoteimpact.org:443:127.0.0.1', '-D', '-', '-o', '/dev/null',
                              'https://remoteimpact.org' + path, capture=True)
                if f'x-remoteimpact-release: {target["release"]}'.lower() not in headers.lower():
                    raise RuntimeError('Caddy did not serve the expected release')
            else:
                req = urllib.request.Request(f'http://127.0.0.1:{target["port"]}{path}',
                    headers={'Host': 'remoteimpact.org', 'X-Forwarded-Proto': 'https'})
                with urllib.request.urlopen(req, timeout=45) as response:
                    if response.status != 200:
                        raise RuntimeError(f'Readiness failed: {path}')
                    response.read()

    def switch(self, target):
        old = self.caddy.read_text()
        new = caddy_config(old, target['port'], target['release'])
        candidate = self.state / 'Caddyfile.next'
        atomic(candidate, new)
        run('caddy', 'validate', '--config', str(candidate), '--adapter', 'caddyfile')
        atomic(self.caddy, new, mode=0o644)
        try:
            run('caddy', 'reload', '--config', str(self.caddy), '--adapter', 'caddyfile')
        except BaseException:
            atomic(self.caddy, old, mode=0o644)
            run('caddy', 'reload', '--config', str(self.caddy), '--adapter', 'caddyfile')
            raise

    def canonical_name(self, active):
        existing = run('docker', 'ps', '-a', '--filter', 'name=^/easyclaw-web-1$', '--format', '{{.ID}}', capture=True).strip()
        if existing and not active['id'].startswith(existing):
            run('docker', 'rename', existing, 'ri-retired-' + existing[:12])
        name = run('docker', 'inspect', '-f', '{{.Name}}', active['id'], capture=True).strip()
        if name != '/easyclaw-web-1':
            run('docker', 'rename', active['id'], 'easyclaw-web-1')

    def rollback(self):
        state = self.load()
        target = state.get('previous')
        if not target:
            raise RuntimeError('No retained release to roll back to')
        run('docker', 'start', target['id'])
        self.ready(target, ('/healthz/', '/', '/jobs/'))
        atomic(self.state / 'pending.json', json.dumps({'active': target, 'previous': state['active']}))
        try:
            self.switch(target)
            self.ready(target, release_header=True)
        except BaseException:
            self.switch(state['active'])
            self.ready(state['active'], release_header=True)
            (self.state / 'pending.json').unlink(missing_ok=True)
            raise
        self.save({'active': target, 'previous': state['active']})
        self.canonical_name(target)
        (self.state / 'pending.json').unlink(missing_ok=True)
        print('Rolled back application traffic; database schema was NOT reversed.', flush=True)

    def stop_previous_if_idle(self):
        state = self.load()
        target = state.get('previous')
        if not target or target['id'] == state['active']['id']:
            return False
        with open(self.state / (target['id'] + '.jobs.lock'), 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print('Previous release is draining scheduled jobs; it will stop when they finish.', flush=True)
                return False
            running = run('docker', 'inspect', '-f', '{{.State.Running}}', target['id'], capture=True).strip()
            if running != 'true':
                return True
            processes = run('docker', 'top', target['id'], '-eo', 'pid,args', capture=True)
            if any('manage.py' in line and 'run_scout_preview_worker' not in line for line in processes.splitlines()):
                return False
            run('docker', 'stop', '--timeout', '65', target['id'])
            print('Stopped idle previous release; rollback image and container retained.', flush=True)
            return True

    def retire(self, target):
        if not target:
            return
        # Jobs started through exec-active hold this shared lock until completion.
        with open(self.state / (target['id'] + '.jobs.lock'), 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Previous release still has a running scheduled job; retry later')
            running = run('docker', 'inspect', '-f', '{{.State.Running}}', target['id'], capture=True).strip()
            if running == 'true':
                processes = run('docker', 'top', target['id'], '-eo', 'pid,args', capture=True)
                if any('manage.py' in line and 'run_scout_preview_worker' not in line for line in processes.splitlines()):
                    raise RuntimeError('Retained release has a management command running; retry later')
                run('docker', 'stop', '--time', '65', target['id'])
            run('docker', 'rm', target['id'])

    def deploy(self, ref=None, allow_migrations=False, check_only=False):
        if not ref and (self.root / 'require-github-deploy').exists():
            raise RuntimeError('Production requires an exact GitHub-tested commit SHA; snapshot deployment is disabled.')
        state = self.load()
        self.ready(state['active'])
        if check_only and allow_migrations:
            raise RuntimeError('Check-only must never apply migrations')
        # Refuse to overwrite the only retained rollback while jobs still use it.
        release_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + os.urandom(3).hex()
        folder = self.state / release_id
        folder.mkdir(mode=0o700)
        source = folder / 'repo'
        source.mkdir()
        if ref:
            if not re.fullmatch(r'[0-9a-f]{40}', ref):
                raise RuntimeError('--ref must be a full Git commit SHA')
            git_source = self.root / 'github-source.git'
            if not git_source.exists():
                git_source = self.root / 'repo'
                paths = ('jobs', 'jobboard', 'blog', 'gigs', 'templates', 'static', 'ml', 'manage.py', 'pyproject.toml', 'uv.lock')
                drift = run('git', '-C', str(git_source), 'status', '--porcelain', '--untracked-files=all', '--', *paths, capture=True)
                if drift.strip():
                    raise RuntimeError('Host application source has uncommitted changes; configure the GitHub source mirror before SHA deployment.')
            archive = folder / 'source.tar'
            with archive.open('wb') as out:
                subprocess.run(['git', '-C', str(git_source), 'archive', ref], stdout=out, check=True)
            with tarfile.open(archive) as contents:
                contents.extractall(source, filter='data')
            archive.unlink()
        else:
            # A manual deployment freezes exactly the currently installed server tree.
            run('rsync', '-a', '--exclude-from=' + str(self.root / 'repo/.dockerignore'),
                '--exclude=.release-static', str(self.root / 'repo') + '/', str(source) + '/')
        if not ref:
            changed = run('rsync', '-anc', '--delete', '--itemize-changes',
                '--exclude-from=' + str(self.root / 'repo/.dockerignore'), '--exclude=.release-static',
                str(self.root / 'repo') + '/', str(source) + '/', capture=True)
            if changed.strip():
                raise RuntimeError('Source changed while copying; retry the deployment to obtain a consistent snapshot')
        recipe = source / 'ops/deploy/Dockerfile.runtime' if ref else self.root / 'Dockerfile.prod'
        shutil.copy2(recipe, folder / 'Dockerfile.prod')
        dep_bytes = b''.join(p.read_bytes() for p in (folder / 'Dockerfile.prod', source / 'pyproject.toml', source / 'uv.lock'))
        dep_hash = hashlib.sha256(dep_bytes).hexdigest()
        runtime = 'ri-runtime:' + dep_hash[:20]
        try:
            run('docker', 'image', 'inspect', runtime, capture=True, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            # Reuse the known live dependency image on the initial adoption only.
            legacy_hash = hashlib.md5(dep_bytes).hexdigest()
            last = self.root / '.last-deps-hash'
            if not (self.state / 'state.json').exists() and last.exists() and last.read_text().strip() == legacy_hash:
                image = run('docker', 'inspect', '-f', '{{.Image}}', state['active']['id'], capture=True).strip()
                run('docker', 'tag', image, runtime)
            else:
                run('docker', 'build', '-t', runtime, '-f', str(folder / 'Dockerfile.prod'), str(folder))
        # Retain hashed assets referenced by pages loaded before the cutover.
        assets = source / '.release-static'
        assets.mkdir()
        run('docker', 'cp', state['active']['id'] + ':/app/staticfiles/.', str(assets))
        shutil.copy2(source / 'ops/deploy/entrypoint-release.sh', source / 'entrypoint-release.sh')
        (source / 'Dockerfile.release').write_text(
            'ARG RUNTIME_IMAGE=easyclaw-web\nFROM ${RUNTIME_IMAGE}\nWORKDIR /app\n'
            'RUN find /app -mindepth 1 -maxdepth 1 ! -name .venv ! -name staticfiles -exec rm -rf {} +\n'
            'COPY . /app/\n'
            'COPY .release-static/ /app/staticfiles/\n'
            'RUN chmod +x /app/entrypoint-release.sh && /app/.venv/bin/python manage.py collectstatic --noinput\n'
            'ENTRYPOINT ["/app/entrypoint-release.sh"]\nCMD []\n')
        # Infrastructure is source-versioned and provisioned only from a checked release.
        performance_env = None
        shared_setup = source / 'ops/deploy/shared_services.py'
        if shared_setup.exists():
            provisioned_env = self.root / 'secrets/performance.env'
            if check_only:
                # A check-only candidate may use already provisioned shared
                # services, but must never create credentials, data dirs or
                # containers as a side effect of validation.
                if provisioned_env.exists():
                    performance_env = provisioned_env
            else:
                run(sys.executable, str(shared_setup), str(self.root))
                performance_env = provisioned_env
        image = 'ri-release:' + release_id.lower()
        run('docker', 'build', '--build-arg', 'RUNTIME_IMAGE=' + runtime, '-f', str(source / 'Dockerfile.release'), '-t', image, str(source))
        port = 8103 if check_only else (8102 if state['active']['port'] == 8101 else 8101)
        if state.get('previous') and not check_only:
            self.retire(state['previous'])
            state['previous'] = None
            self.save(state)
        cid = None
        switched = False
        safe_to_remove = True
        succeeded = False
        runtime_args = []
        if performance_env:
            runtime_args = ['--env-file', str(performance_env),
                '-v', str(self.root / 'cache') + ':/var/cache/remoteimpact',
                '-v', str(self.root / 'performance-logs') + ':/var/log/remoteimpact',
                '-v', str(self.state) + ':/run/remoteimpact-releases:ro']
        try:
            cid = run('docker', 'create', '--name', 'ri-candidate-' + release_id.lower(),
                '--label', 'org.remoteimpact.release=' + release_id,
                '--restart', 'unless-stopped', '--network', 'easyclaw_default',
                '--env-file', str(self.root / '.env'), '-e', 'RELEASE_ID=' + release_id,
                '-p', f'127.0.0.1:{port}:8000',
                '-v', str(self.root / 'media') + ':/app/media',
                '-v', str(self.root / 'secrets/google-indexing.json') + ':/run/secrets/google-indexing.json:ro',
                *runtime_args, '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3',
                image, capture=True).strip()
            target = {'id': cid, 'port': port, 'release': release_id, 'image': image, 'ref': ref}
            # One-off commands use the same immutable image, network, environment and shared media.
            def command(*args):
                return run('docker', 'run', '--rm', '--network', 'easyclaw_default', '--env-file', str(self.root / '.env'),
                    *runtime_args, '-e', 'PGOPTIONS=-c lock_timeout=5s -c statement_timeout=60s',
                    '--entrypoint', '/app/.venv/bin/python', image, '/app/manage.py', *args)
            command('check')
            command('makemigrations', '--check', '--dry-run')
            command('migrate', '--plan')
            try:
                command('migrate', '--check')
            except subprocess.CalledProcessError:
                if not allow_migrations:
                    raise RuntimeError('Pending migrations. Review backward compatibility, then use --allow-migrations; live traffic unchanged.')
                command('migrate', '--noinput')
                command('migrate', '--check')
            if performance_env:
                command('run_scout_preview_worker', '--check')
            run('docker', 'start', cid)
            deadline = time.monotonic() + 90
            while True:
                try:
                    self.ready(target)
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise RuntimeError('Candidate failed readiness within 90 seconds')
                    time.sleep(2)
            self.ready(target, ('/', '/jobs/', '/static/favicon.svg', '/static/tailwind.min.css'))
            # Old callers still send HUP; verify the immutable candidate ignores it.
            before = run('docker', 'top', cid, '-eo', 'pid', capture=True)
            run('docker', 'kill', '--signal', 'HUP', cid)
            time.sleep(0.5)
            after = run('docker', 'top', cid, '-eo', 'pid', capture=True)
            if not before.strip() or before != after:
                raise RuntimeError('Candidate restarted workers on HUP; live traffic unchanged')
            self.ready(target)
            atomic(folder / 'release.json', json.dumps(target, indent=2) + '\n')
            if check_only:
                print('Candidate passed. Check-only: production traffic unchanged.', flush=True)
                return
            command('sync_org_plans')
            # Record intent before the atomic Caddy reload for recovery after interruption.
            atomic(self.state / 'pending.json', json.dumps({'active': target, 'previous': state['active']}))
            switched = True
            safe_to_remove = False
            self.switch(target)
            self.ready(target, ('/healthz/', '/', '/jobs/'), release_header=True)
            self.save({'active': target, 'previous': state['active']})
            succeeded = True
            self.canonical_name(target)
            (self.state / 'pending.json').unlink(missing_ok=True)
            # Only promote deploy tooling from a release which actually passed checks.
            for name in ('blue_green.py', 'exec-active', 'github-deploy'):
                shutil.copy2(source / 'ops/deploy' / name, self.root / name)
                os.chmod(self.root / name, 0o755)
            print(f'Deployed {release_id} on {port}; previous release retained for rollback.', flush=True)
            try:
                self.stop_previous_if_idle()
            except Exception as exc:
                print(f'Previous release cleanup deferred: {exc}', file=sys.stderr, flush=True)
        except BaseException:
            if switched and not succeeded:
                self.switch(state['active'])
                self.ready(state['active'], release_header=True)
                self.save(state)
                safe_to_remove = True
                (self.state / 'pending.json').unlink(missing_ok=True)
            raise
        finally:
            if cid and not succeeded and safe_to_remove:
                run('docker', 'rm', '-f', cid)
            # Source snapshots are retained for audit; never prune images/volumes automatically.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', default='deploy', choices=('deploy', 'fast', 'build', 'migrate', 'rollback', 'status', 'stop-previous'))
    parser.add_argument('--root', default='/opt/easyclaw')
    parser.add_argument('--ref')
    parser.add_argument('--allow-migrations', action='store_true')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    release = Release(args.root)
    with open(release.state / 'deploy.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, 'Another deployment is running; nothing changed.\n')
        if (release.state / 'pending.json').exists():
            parser.exit(1, 'Interrupted cutover found: inspect pending.json and Caddy upstream before continuing.\n')
        if args.mode == 'status':
            print(json.dumps(release.load(), indent=2))
        elif args.mode == 'stop-previous':
            release.stop_previous_if_idle()
        elif args.mode == 'rollback':
            release.rollback()
        else:
            release.deploy(args.ref, args.allow_migrations, args.check_only)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    main()
