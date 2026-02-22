"""
Twitter Job Bot - Posts featured jobs to Twitter/X automatically.

Usage:
    python manage.py post_job_tweet           # Post one random featured job
    python manage.py post_job_tweet --dry-run # Preview without posting
    python manage.py post_job_tweet --count 3 # Post 3 jobs

Requires environment variables:
    TWITTER_API_KEY
    TWITTER_API_SECRET
    TWITTER_ACCESS_TOKEN
    TWITTER_ACCESS_SECRET

Set up cron to run 3x/day:
    0 9,14,19 * * * cd /app && python manage.py post_job_tweet
"""

import random
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from jobs.models import Job, Category

# Category emoji mapping
CATEGORY_EMOJIS = {
    'climate-environment': '🌍',
    'ai-safety': '🤖',
    'global-health': '🏥',
    'biosecurity': '🧬',
    'animal-welfare': '🐾',
    'education': '📚',
    'social-impact': '💡',
    'poverty-alleviation': '🤝',
    'effective-altruism': '⚡',
    'governance-policy': '🏛️',
    'mental-health': '🧠',
    'criminal-justice': '⚖️',
}

# Hashtag sets to rotate
HASHTAG_SETS = [
    ['RemoteJobs', 'ImpactCareers', 'HiringNow'],
    ['RemoteWork', 'SocialImpact', 'JobAlert'],
    ['WorkFromAnywhere', 'PurposeDriven', 'NowHiring'],
    ['RemoteFirst', 'ImpactJobs', 'CareerChange'],
]


class Command(BaseCommand):
    help = 'Post featured jobs to Twitter/X'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview tweet without posting',
        )
        parser.add_argument(
            '--count',
            type=int,
            default=1,
            help='Number of jobs to post (default: 1)',
        )
        parser.add_argument(
            '--category',
            type=str,
            help='Only post jobs from this category slug',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        count = options['count']
        category_slug = options.get('category')

        # Get candidate jobs (posted in last 7 days, not already tweeted)
        jobs = Job.objects.filter(
            is_active=True,
            posted_at__gte=timezone.now() - timedelta(days=7),
        ).select_related('organization', 'category')

        if category_slug:
            jobs = jobs.filter(category__slug=category_slug)

        # Prefer featured jobs, then by salary, then random
        jobs = jobs.order_by('-is_featured', '-salary_max', '?')[:count * 3]

        if not jobs:
            self.stdout.write(self.style.WARNING('No jobs found to tweet'))
            return

        # Pick random subset
        jobs_to_post = random.sample(list(jobs), min(count, len(jobs)))

        for job in jobs_to_post:
            tweet = self.format_tweet(job)
            
            if dry_run:
                self.stdout.write(self.style.SUCCESS(f'\n--- DRY RUN ---'))
                self.stdout.write(tweet)
                self.stdout.write(f'({len(tweet)} chars)')
            else:
                success = self.post_tweet(tweet)
                if success:
                    self.stdout.write(self.style.SUCCESS(f'✓ Posted: {job.title}'))
                else:
                    self.stdout.write(self.style.ERROR(f'✗ Failed: {job.title}'))

    def format_tweet(self, job):
        """Format job as a tweet (max 280 chars)."""
        # Get emoji for category
        cat_emoji = '🌍'
        if job.category:
            cat_emoji = CATEGORY_EMOJIS.get(job.category.slug, '🌍')

        # Format salary
        salary_str = ''
        if job.salary_min and job.salary_max:
            if job.salary_currency == 'GBP':
                salary_str = f'💰 £{job.salary_min//1000}k-£{job.salary_max//1000}k'
            elif job.salary_currency == 'EUR':
                salary_str = f'💰 €{job.salary_min//1000}k-€{job.salary_max//1000}k'
            else:
                salary_str = f'💰 ${job.salary_min//1000}k-${job.salary_max//1000}k'
        elif job.salary_min:
            salary_str = f'💰 ${job.salary_min//1000}k+'

        # Location
        location = job.location or 'Remote'
        if len(location) > 20:
            location = 'Remote'

        # Pick random hashtags
        hashtags = random.choice(HASHTAG_SETS)
        hashtag_str = ' '.join(f'#{h}' for h in hashtags)

        # Build URL
        url = f'https://remoteimpact.org{job.get_absolute_url()}'

        # Build tweet
        lines = [
            f'{cat_emoji} New Remote Role',
            '',
            f'{job.title}',
            f'@ {job.organization.name}',
        ]
        
        if salary_str:
            lines.append(f'{salary_str} | 🌎 {location}')
        else:
            lines.append(f'🌎 {location}')

        lines.extend([
            '',
            f'Apply → {url}',
            '',
            hashtag_str,
        ])

        tweet = '\n'.join(lines)

        # Truncate title if too long
        if len(tweet) > 280:
            max_title_len = len(job.title) - (len(tweet) - 280) - 3
            truncated_title = job.title[:max_title_len] + '...'
            lines[2] = truncated_title
            tweet = '\n'.join(lines)

        return tweet

    def post_tweet(self, text):
        """Post tweet using Twitter API v2."""
        import os
        
        api_key = os.environ.get('TWITTER_API_KEY')
        api_secret = os.environ.get('TWITTER_API_SECRET')
        access_token = os.environ.get('TWITTER_ACCESS_TOKEN')
        access_secret = os.environ.get('TWITTER_ACCESS_SECRET')

        if not all([api_key, api_secret, access_token, access_secret]):
            self.stdout.write(self.style.ERROR(
                'Missing Twitter credentials. Set TWITTER_API_KEY, TWITTER_API_SECRET, '
                'TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET environment variables.'
            ))
            return False

        try:
            import tweepy
        except ImportError:
            self.stdout.write(self.style.ERROR(
                'tweepy not installed. Run: pip install tweepy'
            ))
            return False

        try:
            client = tweepy.Client(
                consumer_key=api_key,
                consumer_secret=api_secret,
                access_token=access_token,
                access_token_secret=access_secret,
            )
            response = client.create_tweet(text=text)
            return response.data is not None
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Twitter API error: {e}'))
            return False
