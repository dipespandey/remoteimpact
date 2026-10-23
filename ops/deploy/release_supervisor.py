"""Supervise web and optional queue worker; fail together and drain on shutdown."""
import os
import signal
import subprocess
import sys
import time

WEB = ['/app/.venv/bin/gunicorn', 'jobboard.wsgi:application', '--config', '/app/ops/deploy/gunicorn_release.py', '--bind', '0.0.0.0:8000', '--workers', '4', '--graceful-timeout', '50', '--timeout', '60', '--access-logfile', '-', '--error-logfile', '-']
WORKER = ['/app/.venv/bin/python', '/app/manage.py', 'run_scout_preview_worker']


def supervise(commands, *, grace=55):
    children = []
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # Immutable release compatibility: HUP is never a deployment mechanism.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    exit_code = 0
    try:
        for command in commands:
            if stopping:
                break
            env = None
            if command == WORKER:
                # Embedding inference must leave CPU capacity for serving pages.
                env = dict(os.environ, OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', TOKENIZERS_PARALLELISM='false')
            children.append(subprocess.Popen(command, start_new_session=True, env=env))
        while not stopping:
            for child in children:
                code = child.poll()
                if code is not None:
                    exit_code = code or 1
                    stopping = True
                    break
            time.sleep(0.2)
    finally:
        for child in children:
            # Signal the process group even when its leader already exited:
            # grandchildren can outlive a failed web/worker master.
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + grace
        for child in children:
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
    return exit_code


if __name__ == '__main__':
    commands = [WEB]
    if os.environ.get('SCOUT_PREVIEW_ASYNC_ENABLED', '').lower() in ('true', '1', 'yes'):
        commands.append(WORKER)
    sys.exit(supervise(commands))
