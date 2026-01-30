from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone
from .models import Job, Category, SeekerProfile


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
        return Category.objects.all().order_by('name')

    def location(self, obj):
        return reverse('jobs:category_landing', args=[obj.slug])

    def lastmod(self, obj):
        from django.utils import timezone
        return timezone.now()

# NOTE: Old CategorySitemap and OrganizationSitemap removed - they were filter URLs
# (?category=x, ?org=x) that all canonicalize to /jobs/, causing Google to see
# them as "alternate pages with proper canonical" and wasting crawl budget.
# If we want these indexed, create dedicated URLs like /jobs/category/climate/


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
            ('jobs:talent_directory', 0.8, 'daily'),
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


# NOTE: LocationSitemap also removed - same reason as above.
# Filter URLs (?location=x) all canonicalize to /jobs/
