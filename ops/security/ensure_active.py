#!/usr/bin/env python3
"""Recover the recorded immutable release after Docker/host maintenance.

Never builds, recreates, migrates or changes the proxy. A cutover interrupted
with pending.json requires deliberate recovery rather than a guessed release.
"""
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.request


def ensure(root=Path('/opt/easyclaw')):
    folder = root / 'releases'
    if not (folder / 'state.json').exists():
        return
    if (folder / 'pending.json').exists():
        raise RuntimeError('Interrupted release cutover requires operator review')
    active = json.loads((folder / 'state.json').read_text())['active']
    if not re.fullmatch('[0-9a-f]{64}', active['id']) or active['port'] not in (8100, 8101, 8102):
        raise RuntimeError('Invalid recorded active release')
    for name in ('easyclaw-db-1', 'ri-cache', active['id']):
        running = subprocess.check_output(['docker', 'inspect', '-f', '{{.State.Running}}', name], text=True).strip()
        if running != 'true':
            subprocess.run(['docker', 'start', name], check=True, stdout=subprocess.DEVNULL)
    request = urllib.request.Request(f"http://127.0.0.1:{active['port']}/healthz/",
                                    headers={'Host':'remoteimpact.org','X-Forwarded-Proto':'https'})
    deadline = time.monotonic() + 60
    while True:
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Recorded active release did not recover health') from None
        time.sleep(1)


if __name__ == '__main__':
    ensure()
