from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.db.models import Count, Max, Q

from .models import Job, Category, SeekerProfile, Organization
from .seo_config import MIN_INDEXABLE_JOBS


def visible_jobs():
    """Jobs that have a public detail page and valid search/indexing value."""
    return Job.objects.filter(Job.availability_filter())


def _live_stats(view_name, config):
    """Count and modification time from the page's own matching public jobs."""
    from .views import seo_pages
    view = getattr(seo_pages, view_name)()
    view.config = config
    return view.get_queryset().order_by().values('pk', 'updated_at').aggregate(
        total=Count('pk'), lastmod=Max('updated_at'))


class JobSitemap(Sitemap):
    """Sitemap for individual job postings - highest priority for SEO."""
    changefreq = "daily"
    priority = 0.8
    limit = 5000  # Google recommends max 50,000 URLs per sitemap

    def items(self):
        return visible_jobs().select_related('organization').order_by('-posted_at')

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
        return Category.objects.annotate(
            live=Count('jobs', filter=Q(jobs__in=visible_jobs())),
            public_lastmod=Max('jobs__updated_at', filter=Q(jobs__in=visible_jobs())),
        ).filter(live__gte=MIN_INDEXABLE_JOBS).order_by('name')

    def location(self, obj):
        return reverse('jobs:category_landing', args=[obj.slug])

    def lastmod(self, obj):
        return obj.public_lastmod


class OrganizationSitemap(Sitemap):
    """Sitemap for organization profile pages."""
    changefreq = "weekly"
    priority = 0.7

    def items(self):
        # Only include organizations with active job listings
        from .views.organizations import OLD_SLUG_REDIRECTS
        return Organization.objects.filter(
            jobs__in=visible_jobs()
        ).exclude(slug__in=OLD_SLUG_REDIRECTS).annotate(
            public_lastmod=Max('jobs__updated_at', filter=Q(jobs__in=visible_jobs()))
        ).order_by('name')

    def location(self, obj):
        return reverse('jobs:organization_profile', args=[obj.slug])

    def lastmod(self, obj):
        return obj.public_lastmod


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

    def priority(self, item):
        return item[1]

    def changefreq(self, item):
        return item[2]

    def lastmod(self, item):
        return None


class GuidesSitemap(Sitemap):
    """Sitemap for pillar and cluster guide pages."""
    changefreq = "weekly"

    def items(self):
        from .views.resources import PILLAR_MAP, published_clusters
        entries = []
        # Pillars (priority 0.8)
        for slug in PILLAR_MAP:
            entries.append(('pillar', slug, None, 0.8))
        # Clusters (priority 0.7)
        for (pillar_slug, cluster_slug) in published_clusters():
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

    def priority(self, item):
        return item[3]

    def lastmod(self, item):
        return None


class RolePagesSitemap(Sitemap):
    """Sitemap for role-based SEO landing pages."""
    changefreq = "daily"
    priority = 0.85

    def items(self):
        from .seo_config import ROLE_SEO_PAGES
        if not hasattr(self, '_page_stats'):
            self._page_stats = {page['slug']: _live_stats('RoleJobsView', page) for page in ROLE_SEO_PAGES}
        return [page for page in ROLE_SEO_PAGES if self._page_stats[page['slug']]['total'] >= MIN_INDEXABLE_JOBS]

    def location(self, item):
        return reverse('jobs:role_jobs', kwargs={'role_slug': item['slug']})

    def lastmod(self, item):
        self.items()
        return self._page_stats[item['slug']]['lastmod']


class KeywordPagesSitemap(Sitemap):
    """Sitemap for keyword-based SEO landing pages (long-tail keywords)."""
    changefreq = "daily"
    priority = 0.85

    def items(self):
        from .seo_config import KEYWORD_SEO_PAGES
        if not hasattr(self, '_page_stats'):
            self._page_stats = {page['slug']: _live_stats('KeywordJobsView', page) for page in KEYWORD_SEO_PAGES}
        return [page for page in KEYWORD_SEO_PAGES if self._page_stats[page['slug']]['total'] >= MIN_INDEXABLE_JOBS]

    def location(self, item):
        return reverse('jobs:keyword_jobs', kwargs={'keyword_slug': item['slug']})

    def lastmod(self, item):
        self.items()
        return self._page_stats[item['slug']]['lastmod']


class StaticSitemap(Sitemap):
    """Sitemap for static pages - homepage gets highest priority."""
    changefreq = "daily"
    priority = 1.0

    def items(self):
        return [
            ('jobs:home', 1.0, 'daily'),
            ('jobs:job_list', 0.9, 'hourly'),
            ('jobs:resources', 0.7, 'weekly'),
            ('jobs:early_careers', 0.8, 'weekly'),
            ('jobs:partner_resources', 0.8, 'weekly'),
            ('jobs:roundup', 0.6, 'weekly'),
            ('jobs:tech_collection', 0.7, 'daily'),
            ('jobs:employers', 0.7, 'monthly'),
            ('jobs:impact_jobs_snapshot', 0.8, 'weekly'),
            ('jobs:agent_evidence_report', 0.7, 'yearly'),
            ('jobs:how_we_check', 0.7, 'weekly'),
            ('jobs:about', 0.5, 'monthly'),
            ('jobs:contact', 0.5, 'monthly'),
            ('gigs:ai_evaluation', 0.6, 'monthly'),
            ('jobs:public_pricing', 0.7, 'monthly'),
            ('jobs:salary_benchmarks', 0.8, 'daily'),  # Salary data page - high value SEO
            ('jobs:all_role_pages', 0.6, 'weekly'),
            ('jobs:all_keyword_pages', 0.6, 'weekly'),
            ('blog:post_list', 0.5, 'weekly'),
            ('gigs:gig_list', 0.8, 'daily'),
            ('jobs:organization_directory', 0.8, 'weekly'),
            ('jobs:all_domains', 0.7, 'weekly'),
        ]

    def location(self, item):
        return reverse(item[0])

    def priority(self, item):
        return item[1]

    def changefreq(self, item):
        return item[2]

    def lastmod(self, item):
        return None


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


class RegionSitemap(Sitemap):
    """Regional landing pages (/remote-jobs/<region>/) with enough open roles to be indexed."""
    changefreq = "daily"
    priority = 0.7

    def items(self):
        from jobs.seo_config import MIN_INDEXABLE_JOBS
        from jobs.views.locations import REGIONS, region_jobs
        return [slug for slug, region in REGIONS.items() if region_jobs(region).count() >= MIN_INDEXABLE_JOBS]

    def location(self, slug):
        return reverse('jobs:region_jobs', args=[slug])
