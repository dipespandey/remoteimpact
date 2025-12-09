# Remote Impact Jobs - Deployment Guide

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

# LLM API Keys for AI-enhanced job parsing (pick one, priority order shown)
# 1. DeepSeek - Cheapest ($0.14-0.28/1M tokens) - https://platform.deepseek.com/api_keys
DEEPSEEK_API_KEY=your_deepseek_api_key

# 2. Groq - Free tier (14,400 req/day) - https://console.groq.com/keys
GROQ_API_KEY=your_groq_api_key

# 3. Mistral - More expensive, fallback
MISTRAL_API_KEY=your_mistral_api_key

# Optional: For Google Custom Search
GOOGLE_CSE_API_KEY=your_google_api_key
GOOGLE_CSE_CX=your_search_engine_id
```

**Cost comparison for ~4000 jobs/day:**
| Provider | Cost/Day | Monthly |
|----------|----------|---------|
| DeepSeek | ~$0.10 | ~$3 |
| Groq | Free | Free |
| Mistral | ~$3.00 | ~$90 |

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
