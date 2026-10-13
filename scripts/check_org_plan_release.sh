#!/bin/bash
set -euo pipefail

# Run from /opt/easyclaw after starting the web service. Its entrypoint may
# still be applying migrations when docker compose up -d returns.
for attempt in {1..30}; do
    if docker compose exec -T web /app/.venv/bin/python /app/manage.py migrate --check; then
        docker compose exec -T web /app/.venv/bin/python /app/manage.py sync_org_plans
        exit 0
    fi
    sleep 2
done
echo "Web service did not finish applying migrations within 60 seconds." >&2
exit 1
