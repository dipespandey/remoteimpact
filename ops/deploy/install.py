#!/usr/bin/env python3
"""Install host entrypoints and migrate cron targets, preserving backups."""
from pathlib import Path
import re
import shutil
import subprocess
import time

root = Path('/opt/easyclaw')
source = root / 'repo/ops/deploy'
backup = root / 'backups' / ('blue-green-tools-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
backup.mkdir(mode=0o700)
for name in ('blue_green.py', 'exec-active', 'deploy.sh'):
    target = root / name
    if target.exists():
        shutil.copy2(target, backup / name)
    shutil.copy2(source / name, target)
    target.chmod(0o755)

# Do not alter commands or schedules: only replace their container-selection prefix.
def migrate(text):
    return re.sub(r'(?<![\w/-])(?:/usr/bin/)?docker exec easyclaw-web-1\b', '/opt/easyclaw/exec-active', text)

result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
if result.returncode == 0:
    (backup / 'crontab').write_text(result.stdout)
    updated = migrate(result.stdout)
    if updated != result.stdout:
        subprocess.run(['crontab', '-'], input=updated, text=True, check=True)
for path in Path('/etc/cron.d').iterdir():
    if not path.is_file():
        continue
    original = path.read_text()
    updated = migrate(original)
    if updated != original:
        shutil.copy2(path, backup / ('cron-' + path.name))
        path.write_text(updated)
for path in (root / 'post_linkedin_jobs.sh',):
    if path.exists():
        original = path.read_text()
        updated = migrate(original)
        if updated != original:
            shutil.copy2(path, backup / path.name)
            path.write_text(updated)
print('Installed blue-green tools and updated scheduled-command targets. Backup:', backup)
