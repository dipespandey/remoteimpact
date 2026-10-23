#!/bin/sh
set -eu
# The web and queue worker share one immutable app container and drain together.
exec /app/.venv/bin/python /app/ops/deploy/release_supervisor.py
