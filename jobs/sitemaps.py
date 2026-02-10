from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone
from .models import Job, Category, SeekerProfile, Organization


class JobSitemap(Sitemap):
    """Sitemap for individual job postings - highest priority for SEO."""
    changefreq = "daily"
    priority = 0.8
    limit = 5000  # Google recommends max 50,000 URLs per sitemap

    def items(self):
        return Job.objects.filter(is_active=True).select_related('organization').order_by('-posted_at')

    def location(self, obj):
        return reverse('jobs:job_detail', args=[obj.slug])

    def lastmod(self, obj):
        return obj.updated_at


class CategorySitemap(Sitemap):
    """Sitemap for category landing pages."""
    changefreq = "weekly"
    priority = 0.9

    def items(self):
        # Only include categories with active jobs
        return Category.objects.filter(
            jobs__is_active=True
        ).distinct().order_by('name')

    def location(self, obj):
        return reverse('jobs:category_landing', args=[obj.slug])

    def lastmod(self, obj):
        # Use the most recent job update time
        latest_job = obj.jobs.filter(is_active=True).order_by('-updated_at').first()
        if latest_job:
            return latest_job.updated_at
        return timezone.now()


class OrganizationSitemap(Sitemap):
    """Sitemap for organization profile pages."""
    changefreq = "weekly"
    priority = 0.7

    def items(self):
        # Only include organizations with active job listings
        return Organization.objects.filter(
            jobs__is_active=True
        ).distinct().order_by('name')

    def location(self, obj):
        return reverse('jobs:organization_profile', args=[obj.slug])

    def lastmod(self, obj):
        # Use the most recent job update time
        latest_job = obj.jobs.filter(is_active=True).order_by('-updated_at').first()
        if latest_job:
            return latest_job.updated_at
        return timezone.now()


class ToolsSitemap(Sitemap):
    """Sitemap for career tools pages - important for SEO traffic."""
    changefreq = "monthly"
    priority = 0.7

    def items(self):
        # List of all tools with their URL names and priorities
        # Only include tools that actually exist in urls.py
        return [
            ('jobs:tools_index', 0.8, 'weekly'),
            ('jobs:salary_to_hourly', 0.7, 'monthly'),
            ('jobs:freelance_rate', 0.7, 'monthly'),
            ('jobs:pto_calculator', 0.7, 'monthly'),
            ('jobs:pay_raise_calculator', 0.7, 'monthly'),
            ('jobs:word_counter', 0.6, 'monthly'),
            ('jobs:pomodoro_timer', 0.6, 'monthly'),
            ('jobs:remote_readiness_quiz', 0.7, 'monthly'),
            ('jobs:skills_gap', 0.7, 'monthly'),
            ('jobs:interview_prep', 0.7, 'monthly'),
            ('jobs:salary_negotiation', 0.7, 'monthly'),
            ('jobs:resignation_letter', 0.7, 'monthly'),
            ('jobs:thank_you_email', 0.7, 'monthly'),
            ('jobs:jd_generator', 0.6, 'monthly'),
            ('jobs:cover_letter_generator', 0.7, 'monthly'),
        ]

    def location(self, item):
        return reverse(item[0])

    def _priority(self, item):
        return item[1]

    def _changefreq(self, item):
        return item[2]

    def lastmod(self, item):
        return timezone.now()


class GuidesSitemap(Sitemap):
    """Sitemap for pillar and cluster guide pages."""
    changefreq = "weekly"

    def items(self):
        from .views.resources import PILLAR_MAP, CLUSTER_MAP
        entries = []
        # Pillars (priority 0.8)
        for slug in PILLAR_MAP:
            entries.append(('pillar', slug, None, 0.8))
        # Clusters (priority 0.7)
        for (pillar_slug, cluster_slug) in CLUSTER_MAP:
            entries.append(('cluster', pillar_slug, cluster_slug, 0.7))
        return entries

    def location(self, item):
        kind, pillar_slug, cluster_slug, _ = item
        if kind == 'pillar':
            return reverse('jobs:pillar_detail', kwargs={'pillar_slug': pillar_slug})
        return reverse('jobs:cluster_detail', kwargs={
            'pillar_slug': pillar_slug,
            'cluster_slug': cluster_slug,
        })

    def _priority(self, item):
        return item[3]

    def lastmod(self, item):
        return timezone.now()


class StaticSitemap(Sitemap):
    """Sitemap for static pages - homepage gets highest priority."""
    changefreq = "daily"
    priority = 1.0

    def items(self):
        return [
            ('jobs:home', 1.0, 'daily'),
            ('jobs:job_list', 0.9, 'hourly'),
            ('jobs:resources', 0.7, 'weekly'),
            ('jobs:post_job', 0.5, 'monthly'),
            ('gigs:gig_list', 0.8, 'daily'),
            ('jobs:organization_directory', 0.8, 'weekly'),
            ('jobs:all_domains', 0.7, 'weekly'),
        ]

    def location(self, item):
        return reverse(item[0])

    def _priority(self, item):
        return item[1]

    def _changefreq(self, item):
        return item[2]

    def lastmod(self, item):
        return timezone.now()


class TalentSitemap(Sitemap):
    """Sitemap for public seeker profiles."""
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return SeekerProfile.objects.filter(
            visibility='public', is_actively_looking=True, wizard_completed=True
        ).select_related('user').order_by('-updated_at')

    def location(self, obj):
        return reverse('jobs:talent_profile', args=[obj.user_id])

    def lastmod(self, obj):
        return obj.updated_at
