#!/bin/sh
set -eu
# Schema changes are a gated one-off deployment step, never a worker startup side effect.
exec /app/.venv/bin/gunicorn jobboard.wsgi:application \
  --config /app/ops/deploy/gunicorn_release.py \
  --bind 0.0.0.0:8000 --workers 4 --graceful-timeout 60 --timeout 60 \
  --access-logfile - --error-logfile -
