"""
Google Indexing API - Request instant indexing for new/updated jobs.

This pings Google to crawl new job pages immediately instead of waiting
for the normal crawl schedule (which can take days).

Usage:
    python manage.py ping_google_indexing                    # Index jobs from last 24h
    python manage.py ping_google_indexing --hours 6          # Jobs from last 6 hours
    python manage.py ping_google_indexing --job-slug xyz     # Index specific job
    python manage.py ping_google_indexing --dry-run          # Preview without sending

Requires:
    - Google Cloud project with Indexing API enabled
    - Service account JSON key file
    - GOOGLE_APPLICATION_CREDENTIALS env var pointing to key file
    
Setup:
    1. Go to Google Cloud Console
    2. Create project & enable "Indexing API"
    3. Create service account & download JSON key
    4. Verify site ownership in Search Console
    5. Add service account email as owner in Search Console
    6. Set GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from jobs.models import Job


class Command(BaseCommand):
    help = 'Ping Google Indexing API for new jobs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=24,
            help='Index jobs posted in last N hours (default: 24)',
        )
        parser.add_argument(
            '--job-slug',
            type=str,
            help='Index a specific job by slug',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview URLs without sending to Google',
        )
        parser.add_argument(
            '--type',
            choices=['URL_UPDATED', 'URL_DELETED'],
            default='URL_UPDATED',
            help='Notification type (default: URL_UPDATED)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        notification_type = options['type']
        
        # Get jobs to index
        if options['job_slug']:
            jobs = Job.objects.filter(slug=options['job_slug'])
        else:
            cutoff = timezone.now() - timedelta(hours=options['hours'])
            jobs = Job.objects.filter(
                is_active=True,
                posted_at__gte=cutoff,
            )

        if not jobs.exists():
            self.stdout.write(self.style.WARNING('No jobs found to index'))
            return

        # Build URLs
        base_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org')
        urls = [f"{base_url}{job.get_absolute_url()}" for job in jobs]

        self.stdout.write(f'Found {len(urls)} URLs to index\n')

        if dry_run:
            self.stdout.write(self.style.SUCCESS('DRY RUN - URLs that would be indexed:'))
            for url in urls:
                self.stdout.write(f'  {url}')
            return

        # Send to Google
        success, failed = self.send_to_google(urls, notification_type)
        
        self.stdout.write(self.style.SUCCESS(f'\n✓ Indexed: {success}'))
        if failed:
            self.stdout.write(self.style.ERROR(f'✗ Failed: {failed}'))

    def send_to_google(self, urls, notification_type):
        """Send URLs to Google Indexing API."""
        import os
        
        credentials_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        
        if not credentials_path:
            self.stdout.write(self.style.ERROR(
                'GOOGLE_APPLICATION_CREDENTIALS not set.\n'
                'Set this to the path of your service account JSON key file.'
            ))
            return 0, len(urls)

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError:
            self.stdout.write(self.style.ERROR(
                'Google API client not installed.\n'
                'Run: pip install google-api-python-client google-auth'
            ))
            return 0, len(urls)

        try:
            credentials = service_account.Credentials.from_service_account_file(
                credentials_path,
                scopes=['https://www.googleapis.com/auth/indexing']
            )
            service = build('indexing', 'v3', credentials=credentials)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to initialize Google API: {e}'))
            return 0, len(urls)

        success = 0
        failed = 0

        for url in urls:
            try:
                body = {
                    'url': url,
                    'type': notification_type
                }
                response = service.urlNotifications().publish(body=body).execute()
                
                if 'urlNotificationMetadata' in response:
                    self.stdout.write(f'  ✓ {url}')
                    success += 1
                else:
                    self.stdout.write(self.style.WARNING(f'  ? {url} - unexpected response'))
                    failed += 1
                    
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ {url} - {e}'))
                failed += 1

        return success, failed


# Also create a signal handler to auto-ping on new jobs
def ping_on_job_create(sender, instance, created, **kwargs):
    """Signal handler to ping Google when a new job is created."""
    if not created:
        return
    
    import os
    if not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        return  # Skip if not configured
    
    from django.core.management import call_command
    try:
        call_command('ping_google_indexing', job_slug=instance.slug)
    except Exception:
        pass  # Don't break job creation if indexing fails
