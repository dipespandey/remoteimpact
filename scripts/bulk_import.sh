#!/bin/bash
#
# Bulk job import script for Remote Impact Jobs
# Run this ONCE to populate the database with all existing jobs
#
# This uses DeepSeek for AI parsing (cheapest option for bulk)
#
# Usage:
#   ./scripts/bulk_import.sh
#
# Cost estimate: ~$0.50-1.00 for ~4000 jobs
#

set -e

# Configuration
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"

# Change to project directory
cd "$PROJECT_DIR"

echo "========================================"
echo "Remote Impact Jobs - Bulk Import"
echo "Started: $(date)"
echo "Using DeepSeek for AI parsing (cheapest)"
echo "========================================"

# Step 1: Import from aggregator APIs with DeepSeek AI parsing
echo ""
echo "[Step 1/4] Importing from aggregator APIs with AI parsing..."
uv run python manage.py import_remote_jobs --use-ai --provider deepseek --batch-size 10

# Step 2: Import job URLs from Google Search (if configured)
echo ""
echo "[Step 2/4] Importing URLs from Google Search..."
uv run python manage.py import_google_jobs --unified --num-results 200 2>/dev/null || echo "  (Skipped - Google Search not configured)"

# Step 3: Crawl job details from discovered URLs (uses APIs, no AI needed)
echo ""
echo "[Step 3/4] Crawling job details from discovered URLs..."
uv run python manage.py crawl_jobs --limit 500 2>/dev/null || echo "  (Skipped - no pending crawl jobs)"

# Step 4: Seed categories if needed
echo ""
echo "[Step 4/4] Ensuring categories exist..."
uv run python manage.py shell -c "
from jobs.models import Category
from jobs.constants import IMPACT_AREAS
for area in IMPACT_AREAS:
    Category.objects.get_or_create(
        slug=area['slug'],
        defaults={'name': area['name'], 'description': '', 'icon': area.get('icon', '')}
    )
print(f'Categories: {Category.objects.count()}')
"

# Summary
echo ""
echo "========================================"
echo "Bulk Import Complete: $(date)"
echo "========================================"
echo ""

# Show job counts
uv run python manage.py shell -c "
from jobs.models import Job, Category, Organization
print(f'Total active jobs: {Job.objects.filter(is_active=True).count()}')
print(f'Organizations: {Organization.objects.count()}')
print(f'Categories: {Category.objects.count()}')
"
