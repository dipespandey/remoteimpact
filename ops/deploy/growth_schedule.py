"""Install the bounded active-release growth worker without changing other cron jobs."""
from pathlib import Path
import os

CRON = '''# Managed by RemoteImpact releases. Research/drafts/measurement only.
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/15 * * * * root /opt/easyclaw/exec-active /app/.venv/bin/python /app/manage.py shell -c 'from django.core.management import get_commands, call_command; "run_growth_cycle" in get_commands() and call_command("run_growth_cycle", due=True)' 2>&1 | /usr/bin/logger -t remoteimpact-growth
'''


def install(directory='/etc/cron.d'):
    path = Path(directory) / 'remoteimpact-growth'
    if path.exists() and path.read_text() == CRON:
        return
    if path.exists() and not path.read_text().startswith('# Managed by RemoteImpact releases.'):
        raise RuntimeError('Existing growth schedule is not release-managed; retained.')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(CRON)
    temporary.chmod(0o644)
    os.replace(temporary, path)
