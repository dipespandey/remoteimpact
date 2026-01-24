#!/bin/bash
# Daily job import, crawl, and cleanup script
# Run via cron: 0 6 * * * /app/scripts/daily_jobs.sh >> /var/log/daily_jobs.log 2>&1

set -e

cd /app

echo "=========================================="
echo "Daily Jobs - $(date)"
echo "=========================================="

# 1. Import new jobs from all sources (skip existing)
echo ""
echo "[1/4] Importing new jobs..."
/app/.venv/bin/python manage.py import_remote_jobs --new-only

# 2. Crawl job details for jobs needing updates
echo ""
echo "[2/4] Crawling job details..."
/app/.venv/bin/python manage.py crawl_jobs --delay 0.3

# 3. Deactivate expired jobs
echo ""
echo "[3/4] Deactivating expired jobs..."
/app/.venv/bin/python manage.py deactivate_expired_jobs

# 4. Generate embeddings for new jobs
echo ""
echo "[4/4] Generating embeddings for new jobs..."
/app/.venv/bin/python manage.py embed_jobs --only-missing

echo ""
echo "=========================================="
echo "Daily Jobs Complete - $(date)"
echo "=========================================="
