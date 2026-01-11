from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone
from .models import Job, Category, Organization


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
    """Sitemap for category/impact area pages."""
    changefreq = "daily"
    priority = 0.7

    def items(self):
        return Category.objects.all()

    def location(self, obj):
        return reverse('jobs:job_list') + f'?category={obj.slug}'

    def lastmod(self, obj):
        # Get the most recent job in this category
        latest_job = Job.objects.filter(category=obj, is_active=True).order_by('-updated_at').first()
        if latest_job:
            return latest_job.updated_at
        return timezone.now()


class OrganizationSitemap(Sitemap):
    """Sitemap for organization pages (if they have dedicated pages)."""
    changefreq = "weekly"
    priority = 0.5

    def items(self):
        # Only include organizations with active jobs
        return Organization.objects.filter(jobs__is_active=True).distinct()

    def location(self, obj):
        return reverse('jobs:job_list') + f'?org={obj.slug}'

    def lastmod(self, obj):
        latest_job = obj.jobs.filter(is_active=True).order_by('-updated_at').first()
        if latest_job:
            return latest_job.updated_at
        return obj.created_at


class StaticSitemap(Sitemap):
    """Sitemap for static pages - homepage gets highest priority."""
    changefreq = "daily"
    priority = 1.0

    def items(self):
        return [
            ('jobs:home', 1.0, 'daily'),
            ('jobs:job_list', 0.9, 'hourly'),
            ('jobs:resources', 0.7, 'weekly'),
            ('jobs:applicant_assistant', 0.6, 'weekly'),
            ('jobs:post_job', 0.5, 'monthly'),
            ('gigs:gig_list', 0.8, 'daily'),
        ]

    def location(self, item):
        return reverse(item[0])

    def priority(self, item):
        return item[1]

    def changefreq(self, item):
        return item[2]

    def lastmod(self, item):
        # Static pages - use current time for frequently changing pages
        return timezone.now()


class LocationSitemap(Sitemap):
    """Sitemap for location-filtered job pages."""
    changefreq = "daily"
    priority = 0.6

    def items(self):
        # Get unique locations with active jobs
        locations = Job.objects.filter(is_active=True).values_list('location', flat=True).distinct()
        return list(locations)[:100]  # Limit to top 100 locations

    def location(self, obj):
        from urllib.parse import quote
        return reverse('jobs:job_list') + f'?location={quote(obj)}'

    def lastmod(self, obj):
        latest_job = Job.objects.filter(location=obj, is_active=True).order_by('-updated_at').first()
        if latest_job:
            return latest_job.updated_at
        return timezone.now()
