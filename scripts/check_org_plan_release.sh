#!/bin/bash
set -euo pipefail

# Run against the active release. Blue-green deployments gate migrations
# before cutover; retries also support initial installation.
for attempt in {1..30}; do
    if /opt/easyclaw/exec-active /app/.venv/bin/python /app/manage.py migrate --check; then
        /opt/easyclaw/exec-active /app/.venv/bin/python /app/manage.py sync_org_plans
        exit 0
    fi
    sleep 2
done
echo "Web service did not finish applying migrations within 60 seconds." >&2
exit 1
