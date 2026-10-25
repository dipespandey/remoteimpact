#!/usr/bin/env python3
"""Weekly, encrypted, atomic backups. The decryption identity stays off-server."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path('/opt/easyclaw')
DEST = ROOT / 'backups/encrypted'


def encrypted_pipeline(command, destination, recipient):
    partial = destination.with_suffix(destination.suffix + '.partial')
    producer = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with partial.open('wb') as output:
            partial.chmod(0o600)
            result = subprocess.run(['age', '-r', recipient], stdin=producer.stdout, stdout=output,
                                    stderr=subprocess.PIPE, timeout=1800)
        producer.stdout.close()
        error = producer.communicate(timeout=60)[1]
        if result.returncode or producer.returncode:
            raise RuntimeError('Backup pipeline failed; previous backups retained')
        partial.replace(destination)
    finally:
        if producer.poll() is None:
            producer.kill()
            producer.wait()
        partial.unlink(missing_ok=True)


def main():
    DEST.mkdir(mode=0o700, parents=True, exist_ok=True)
    DEST.chmod(0o700)
    recipient = (ROOT / 'secrets/backup-recipient.txt').read_text().strip()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    jobs = [
        ('database', ['docker', 'exec', 'easyclaw-db-1', 'pg_dump', '-U', 'dokploy', '-d', 'remoteimpact', '--format=custom']),
        ('media', ['tar', '-C', str(ROOT), '-czf', '-', 'media']),
        ('configuration', ['tar', '-C', str(ROOT), '-czf', '-', '.env', 'docker-compose.yml', 'secrets']),
    ]
    rows = []
    for name, command in jobs:
        file = DEST / (name + '-' + stamp + '.age')
        encrypted_pipeline(command, file, recipient)
        with file.open('rb') as handle:
            checksum = hashlib.file_digest(handle, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else None
        # Python 3.10 on the host lacks file_digest.
        if checksum is None:
            digest = hashlib.sha256()
            with file.open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
            checksum = digest.hexdigest()
        rows.append({'name':file.name,'bytes':file.stat().st_size,'sha256':checksum})
    manifest = DEST / ('manifest-' + stamp + '.json')
    manifest.write_text(json.dumps(rows) + '\n')
    manifest.chmod(0o600)
    # Keep eight complete weekly sets. Never prune until the new set is complete.
    for old in sorted(DEST.glob('manifest-*.json'), reverse=True)[8:]:
        for row in json.loads(old.read_text()):
            (DEST / row['name']).unlink(missing_ok=True)
        old.unlink()
    print(json.dumps({'backup_complete':True,'files':len(rows),'encrypted':True,'stamp':stamp}))


if __name__ == '__main__':
    main()
