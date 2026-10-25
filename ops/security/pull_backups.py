#!/usr/bin/env python3
"""Mac-side backup pull: never stores plaintext or creates extra server backups."""
import hashlib
import json
from pathlib import Path
import subprocess

PRIVATE = Path.home() / '.config/remoteimpact-backups'
DEST = Path.home() / 'Documents/RemoteImpact Backups'
SSH = ['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'IdentitiesOnly=yes',
       '-i', str(PRIVATE / 'pull-key'), 'root@46.224.131.113']


def main():
    DEST.mkdir(mode=0o700, parents=True, exist_ok=True)
    DEST.chmod(0o700)
    result = subprocess.run(SSH + ['list'], check=True, capture_output=True, text=True, timeout=30)
    rows = json.loads(result.stdout)
    downloaded = 0
    for row in rows:
        name = row['name']
        if Path(name).name != name or not name.endswith('.age'):
            raise RuntimeError('Invalid backup manifest')
        file = DEST / name
        if file.exists():
            continue
        partial = file.with_suffix('.age.partial')
        with partial.open('wb') as output:
            partial.chmod(0o600)
            subprocess.run(SSH + ['get ' + name], stdout=output, check=True, timeout=1800)
        digest = hashlib.sha256()
        with partial.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != row['sha256']:
            partial.unlink()
            raise RuntimeError('Encrypted backup checksum mismatch')
        partial.replace(file)
        downloaded += 1
    print(json.dumps({'downloaded':downloaded,'server_backup_schedule':'weekly','encrypted':True}))


if __name__ == '__main__':
    main()
