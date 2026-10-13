"""Free first-party SEO operations evidence; never invents search traffic."""
import json
import os
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job, Category
from jobs.seo_config import MIN_INDEXABLE_JOBS, ROLE_SEO_PAGES
from jobs.views.seo_pages import RoleJobsView


class Command(BaseCommand):
    help = 'Report public inventory and indexing acknowledgements, without paid APIs or sending notifications.'

    def handle(self, *args, **options):
        now = timezone.now()
        public = Job.objects.filter(Job.availability_filter(now=now))
        categories = list(Category.objects.annotate(
            count=Count('jobs', filter=Q(jobs__in=public))).filter(count__gt=0)
            .values('name', 'slug', 'count').order_by('-count'))
        roles = []
        for config in ROLE_SEO_PAGES:
            view = RoleJobsView()
            view.config = config
            count = view.get_queryset().count()
            roles.append({'role': config['role'], 'url': settings.SITE_URL.rstrip('/') +
                reverse('jobs:role_jobs', args=[config['slug']]), 'current_jobs': count,
                'meets_site_indexability_threshold': count >= MIN_INDEXABLE_JOBS})
        providers = {}
        for provider in ['google', 'indexnow']:
            providers[provider] = {
                'urls_ever_acknowledged': Job.objects.filter(**{f'indexing_state__has_key': provider}).count(),
                'acknowledged_in_last_7_days': Job.objects.filter(**{
                    f'indexing_state__{provider}__submitted_at__gte': (now-timedelta(days=7)).isoformat()}).count(),
                'state_means': 'Last notification accepted, not proof of crawling, indexing, ranking or traffic'}
        result = {'generated_at': now.isoformat(), 'site': settings.SITE_URL,
            'inventory': {'current_jobs': public.count(), 'posted_last_7_days': public.filter(
                posted_at__gte=now-timedelta(days=7)).count(), 'employers': public.values('organization_id').distinct().count(),
                'sources': list(public.values('source').annotate(count=Count('pk')).order_by('-count'))},
            'categories': categories, 'roles': sorted(roles, key=lambda row: -row['current_jobs']),
            'indexing': {'indexnow_enabled': settings.INDEXNOW_ENABLED,
                'google_credentials_configured': bool(os.getenv('GOOGLE_INDEXING_CREDENTIALS_JSON') or
                                                      os.getenv('GOOGLE_APPLICATION_CREDENTIALS')),
                'providers': providers},
            'measurement': {'organic_search_clicks': None, 'organic_sessions': None, 'organic_scout_signups': None,
                'state': 'Import verified Search Console/GA4 evidence separately; inventory and notifications are not traffic.',
                'goal': '10x qualified organic traffic from a recorded baseline; no forecast or guaranteed date.'}}
        self.stdout.write(json.dumps(result, indent=2))
