#!/bin/bash
#
# Daily job import script for Remote Impact Jobs
# Run this via cron to keep job listings fresh
#
# Recommended cron schedule (add to crontab with `crontab -e`):
#   0 2 * * * /path/to/remoteimpact/scripts/daily_import.sh >> /var/log/remoteimpact/import.log 2>&1
#
# This script:
# 1. Imports jobs from aggregator APIs (80000hours, idealist, reliefweb, climatebase, probablygood)
# 2. Imports job URLs from Google Search (greenhouse, lever, ashby)
# 3. Crawls the Google Search URLs to fetch full job details
# 4. Uses Groq for AI parsing (free tier - good for daily incremental imports)
#

set -e

# Configuration
PROJECT_DIR="${PROJECT_DIR:-/path/to/remoteimpact}"
USE_AI="${USE_AI:-true}"  # Default to using AI for daily imports
AI_PROVIDER="${AI_PROVIDER:-groq}"  # Default to Groq (free tier)
LOG_DATE=$(date +%Y-%m-%d_%H-%M-%S)

# Change to project directory
cd "$PROJECT_DIR"

echo "========================================"
echo "Remote Impact Jobs - Daily Import"
echo "Started: $(date)"
echo "AI Provider: $AI_PROVIDER"
echo "========================================"

# Step 1: Import from aggregator APIs
echo ""
echo "[Step 1/4] Importing from aggregator APIs..."
if [ "$USE_AI" = "true" ]; then
    uv run python manage.py import_remote_jobs --use-ai --provider "$AI_PROVIDER" --batch-size 5
else
    uv run python manage.py import_remote_jobs
fi

# Step 2: Import job URLs from Google Search (if configured)
echo ""
echo "[Step 2/4] Importing URLs from Google Search..."
uv run python manage.py import_google_jobs --unified --num-results 100 2>/dev/null || echo "  (Skipped - Google Search not configured)"

# Step 3: Crawl newly discovered jobs (uses APIs, no AI needed)
echo ""
echo "[Step 3/4] Crawling job details from discovered URLs..."
uv run python manage.py crawl_jobs --limit 200 2>/dev/null || echo "  (No pending jobs to crawl)"

# Step 4: Clean up expired/stale jobs
echo ""
echo "[Step 4/4] Cleaning up expired and stale jobs..."
uv run python manage.py cleanup_jobs --days 45

# Summary
echo ""
echo "========================================"
echo "Import Complete: $(date)"
echo "========================================"
echo ""

# Show job counts
uv run python manage.py shell -c "from jobs.models import Job; print(f'Total active jobs: {Job.objects.filter(is_active=True).count()}')"
