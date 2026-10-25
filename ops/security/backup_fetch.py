#!/usr/bin/env python3
"""Forced SSH command: list or fetch complete encrypted backup files only."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path('/opt/easyclaw/backups/encrypted')


def handle(command):
    if command == 'list':
        rows = []
        for manifest in sorted(ROOT.glob('manifest-*.json')):
            rows.extend(json.loads(manifest.read_text()))
        sys.stdout.write(json.dumps(rows) + '\n')
    elif re.fullmatch(r'get (?:database|media|configuration)-[0-9]{8}T[0-9]{6}Z\.age', command):
        file = ROOT / command[4:]
        fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                sys.stdout.buffer.write(chunk)
    else:
        raise SystemExit('Only encrypted backup list/get is allowed')


if __name__ == '__main__':
    handle(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
