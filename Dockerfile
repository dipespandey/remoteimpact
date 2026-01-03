# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install cron
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_SYSTEM_PYTHON=1

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Create data directory for persistent storage
RUN mkdir -p /data

# Collect static files
RUN uv run python manage.py collectstatic --noinput

# Create import script that will be called by cron
RUN echo '#!/bin/bash\n\
source /app/.env.cron\n\
cd /app\n\
/usr/local/bin/uv run python manage.py import_remote_jobs --new-only --use-ai --provider deepseek\n\
' > /app/run_import.sh && chmod +x /app/run_import.sh

# Setup cron job for daily imports (6 AM UTC)
RUN echo "0 6 * * * /app/run_import.sh >> /var/log/import_jobs.log 2>&1" > /etc/cron.d/import-jobs \
    && chmod 0644 /etc/cron.d/import-jobs \
    && crontab /etc/cron.d/import-jobs

# Create entrypoint script
RUN echo '#!/bin/bash\n\
set -e\n\
\n\
# Export environment variables for cron\n\
printenv > /app/.env.cron\n\
\n\
# Start cron in background\n\
cron\n\
\n\
# Run migrations\n\
uv run python manage.py migrate --noinput\n\
\n\
# Start gunicorn\n\
exec uv run gunicorn jobboard.wsgi:application --bind 0.0.0.0:8000 --workers 2\n\
' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Expose port
EXPOSE 8000

# Run entrypoint
CMD ["/app/entrypoint.sh"]
