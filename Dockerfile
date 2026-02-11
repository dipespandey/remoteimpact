# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install cron and Playwright browser dependencies
RUN apt-get update && apt-get install -y \
    cron \
    fonts-dejavu-core \
    # Playwright Chromium dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_SYSTEM_PYTHON=1

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Install Playwright Chromium browser (headless)
RUN uv run playwright install chromium

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
/usr/local/bin/uv run python manage.py import_remote_jobs --new-only --use-ai --provider deepseek --batch-size 20\n\
' > /app/run_import.sh && chmod +x /app/run_import.sh

# Create job alerts script
RUN echo '#!/bin/bash\n\
source /app/.env.cron\n\
cd /app\n\
/usr/local/bin/uv run python manage.py send_job_alerts\n\
' > /app/run_alerts.sh && chmod +x /app/run_alerts.sh

# Setup cron jobs:
# - 6 AM UTC: Import new jobs
# - 8 AM UTC: Send job alerts (daily alerts every day, weekly alerts on Mondays)
RUN echo "0 6 * * * /app/run_import.sh >> /var/log/import_jobs.log 2>&1" > /etc/cron.d/remoteimpact-crons \
    && echo "0 8 * * * /app/run_alerts.sh >> /var/log/job_alerts.log 2>&1" >> /etc/cron.d/remoteimpact-crons \
    && chmod 0644 /etc/cron.d/remoteimpact-crons \
    && crontab /etc/cron.d/remoteimpact-crons

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
