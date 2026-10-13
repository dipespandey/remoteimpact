# Remote Impact Jobs - Deployment Guide

## Organization plans and API limits

The deployment workflow waits for migrations, refreshes organization-plan featuring,
and installs `ops/remoteimpact-org-plans.cron` on the host. The hourly tasks refresh
featuring and prune expired API rate counters. For a manual deployment, install it
as `/etc/cron.d/remoteimpact-org-plans` (root:root, mode 0644), confirm the host cron
service is running, and run `python manage.py sync_org_plans` after migrations.
The cron entry uses the existing `easyclaw-web-1` container name; adjust it if the
Compose project name changes.

Set `STRIPE_WEBHOOK_SECRET` and subscribe the Stripe webhook to checkout completion,
async payment success, and subscription creation/update/deletion/pause/resume events.
Stripe's billing portal needs an active configuration for cancellation and payment
method management. Existing organization subscriptions cannot start another plan
through checkout; plan changes should be handled through billing/support.

Release verification:

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run python manage.py test --noinput
DEBUG=False uv run python manage.py check --deploy --fail-level WARNING
cd frontend
npm ci
npm run build
```

Prefetch the embedding model using the README command before an offline test run.
Use a pgvector PostgreSQL database through `DATABASE_URL` to include concurrency
tests. The production settings check requires a real, strong `SECRET_KEY`; the
local development default is deliberately unsuitable for deployment.

## Cron Setup for Daily Job Imports

### 1. Edit the daily import script

Update the `PROJECT_DIR` in `scripts/daily_import.sh`:

```bash
# Edit the script
nano scripts/daily_import.sh

# Change this line to your actual path:
PROJECT_DIR="${PROJECT_DIR:-/home/user/remoteimpact}"
```

### 2. Create log directory

```bash
sudo mkdir -p /var/log/remoteimpact
sudo chown $USER:$USER /var/log/remoteimpact
```

### 3. Set up cron job

```bash
# Edit crontab
crontab -e

# Add this line to run daily at 6 AM:
0 6 * * * /home/user/remoteimpact/scripts/daily_import.sh >> /var/log/remoteimpact/import.log 2>&1

# Or with AI parsing enabled (slower but enriches job descriptions):
0 6 * * * USE_AI=true /home/user/remoteimpact/scripts/daily_import.sh >> /var/log/remoteimpact/import.log 2>&1
```

### 4. Environment Variables

Make sure your production `.env` file has these keys:

```bash
# Required for job imports
DJANGO_SETTINGS_MODULE=jobboard.settings

# LLM API key and model for AI-enhanced job parsing
# DeepSeek V4 Flash is the low-cost production default:
# https://platform.deepseek.com/api_keys
DEEPSEEK_API_KEY=your_deepseek_api_key
# Optional; defaults to deepseek-v4-flash
DEEPSEEK_MODEL=deepseek-v4-flash


# Optional: For Google Custom Search
GOOGLE_CSE_API_KEY=your_google_api_key
GOOGLE_CSE_CX=your_search_engine_id
```

DeepSeek V4 Flash pricing varies by cache hit and peak/off-peak hours. The
application uses the non-thinking mode for routine extraction and matching,
with thinking explicitly disabled for routine tasks. This is the lowest-cost
configuration. See the official pricing page for
current rates: https://api-docs.deepseek.com/quick_start/pricing/

### 5. Test the script manually

```bash
cd /home/user/remoteimpact
PROJECT_DIR=/home/user/remoteimpact ./scripts/daily_import.sh
```

## Management Commands Reference

### Import jobs from aggregator APIs
```bash
# All sources
uv run python manage.py import_remote_jobs

# Specific source
uv run python manage.py import_remote_jobs --source 80000hours

# With AI enrichment (slower, costs money)
uv run python manage.py import_remote_jobs --use-ai --batch-size 5
```

### Import job URLs from Google Search
```bash
# All job boards (greenhouse, lever, ashby)
uv run python manage.py import_google_jobs --unified

# Specific board
uv run python manage.py import_google_jobs --board greenhouse
```

### Crawl job details from discovered URLs
```bash
# Crawl pending jobs
uv run python manage.py crawl_jobs --limit 200

# With AI enrichment
uv run python manage.py crawl_jobs --limit 200 --use-ai
```

### Clean up old jobs
```bash
# Deactivate expired and stale (45+ days) jobs
uv run python manage.py cleanup_jobs

# Custom threshold
uv run python manage.py cleanup_jobs --days 30

# Preview changes
uv run python manage.py cleanup_jobs --dry-run
```

## Log Rotation (optional)

Add to `/etc/logrotate.d/remoteimpact`:

```
/var/log/remoteimpact/*.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
}
```

## Monitoring

Check cron is running:
```bash
# View recent cron logs
grep CRON /var/log/syslog | tail -20

# View import logs
tail -f /var/log/remoteimpact/import.log
```
