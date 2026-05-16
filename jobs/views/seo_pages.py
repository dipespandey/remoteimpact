"""
Programmatic SEO Views

Handles dynamic SEO landing pages for role-based searches:
- /remote-software-engineer-jobs/
- /remote-data-analyst-jobs/
- etc.
"""

from django.views.generic import ListView
from django.http import Http404
from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta

from ..models import Job, Category, Organization
from ..seo_config import (
    get_role_page_config, get_all_role_slugs, ROLE_SEO_PAGES,
    get_keyword_page_config, get_all_keyword_slugs, KEYWORD_SEO_PAGES,
)


def visible_jobs():
    now = timezone.now()
    cutoff = now - timedelta(days=180)
    return Job.objects.filter(is_active=True).exclude(
        expires_at__lt=now,
    ).exclude(
        expires_at__isnull=True,
        posted_at__lt=cutoff,
    )


class RoleJobsView(ListView):
    """
    Dynamic SEO landing page for role-based job searches.
    
    URL: /remote-{role}-jobs/
    """
    template_name = "jobs/seo/role_jobs.html"
    context_object_name = "jobs"
    paginate_by = 25

    def dispatch(self, request, *args, **kwargs):
        self.role_slug = kwargs.get('role_slug')
        self.config = get_role_page_config(self.role_slug)
        
        if not self.config:
            raise Http404("Role page not found")
        
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        """Filter jobs matching the role patterns."""
        patterns = self.config['patterns']
        
        # Build Q objects for case-insensitive title matching
        q_objects = Q()
        for pattern in patterns:
            q_objects |= Q(title__icontains=pattern)
        
        return visible_jobs().filter(q_objects).select_related(
            'organization', 'category'
        ).order_by('-posted_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        job_count = self.get_queryset().count()
        
        # Page metadata
        context['config'] = self.config
        context['job_count'] = job_count
        context['h1'] = self.config['h1']
        context['page_title'] = self.config['title'].format(count=job_count)
        context['meta_description'] = self.config['meta_desc'].format(count=job_count)
        context['intro'] = self.config['intro']
        context['icon'] = self.config['icon']
        context['role_name'] = self.config['role']
        
        jobs_qs = self.get_queryset()
        category_rows = (
            jobs_qs.values('category__name', 'category__slug')
            .exclude(category__name__isnull=True)
            .annotate(count=Count('id'))
            .order_by('-count')[:8]
        )
        context['category_breakdown'] = [
            (
                row['category__name'],
                {'slug': row['category__slug'], 'count': row['count']},
            )
            for row in category_rows
        ]
        
        # Salary stats
        jobs_with_salary = jobs_qs.exclude(salary_min__isnull=True)
        if jobs_with_salary.exists():
            from django.db.models import Avg, Min, Max
            salary_stats = jobs_with_salary.aggregate(
                avg_min=Avg('salary_min'),
                avg_max=Avg('salary_max'),
                lowest=Min('salary_min'),
                highest=Max('salary_max'),
            )
            context['salary_stats'] = {
                'avg_min': int(salary_stats['avg_min'] or 0),
                'avg_max': int(salary_stats['avg_max'] or 0),
                'lowest': int(salary_stats['lowest'] or 0),
                'highest': int(salary_stats['highest'] or 0),
                'count': jobs_with_salary.count(),
            }
        
        # Featured jobs (with salary, recent)
        context['featured_jobs'] = jobs_qs.exclude(
            salary_min__isnull=True
        ).order_by('-salary_max', '-posted_at')[:6]
        
        # Related role pages
        context['related_roles'] = [
            page for page in ROLE_SEO_PAGES 
            if page['slug'] != self.role_slug
        ][:6]
        
        # Categories for filter
        context['categories'] = Category.objects.all()
        
        return context


class AllRolePagesView(ListView):
    """
    Index page showing all role-based SEO pages.
    
    URL: /remote-jobs/ or /careers/
    """
    template_name = "jobs/seo/role_index.html"
    context_object_name = "role_pages"

    def get_queryset(self):
        """Get all role pages with job counts."""
        result = []
        for config in ROLE_SEO_PAGES:
            patterns = config['patterns']
            q_objects = Q()
            for pattern in patterns:
                q_objects |= Q(title__icontains=pattern)
            
            count = visible_jobs().filter(q_objects).count()
            
            if count > 0:
                result.append({
                    **config,
                    'job_count': count,
                })
        
        # Sort by job count
        result.sort(key=lambda x: x['job_count'], reverse=True)
        return result

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = "Browse Remote Jobs by Role | Remote Impact"
        context['meta_description'] = "Explore remote job opportunities by role type. Find software engineer, data scientist, product manager, and more positions at impact organizations."
        context['total_jobs'] = visible_jobs().count()
        return context


# =============================================================================
# KEYWORD SEO PAGES
# Target long-tail keywords from keyword research
# =============================================================================

class KeywordJobsView(ListView):
    """
    Dynamic SEO landing page for keyword-based job searches.
    
    URL: /impact/{keyword-slug}/
    Examples:
    - /impact/remote-jobs-with-purpose/
    - /impact/remote-nonprofit-jobs/
    - /impact/remote-humanitarian-jobs/
    """
    template_name = "jobs/seo/keyword_jobs.html"
    context_object_name = "jobs"
    paginate_by = 25

    def dispatch(self, request, *args, **kwargs):
        self.keyword_slug = kwargs.get('keyword_slug')
        self.config = get_keyword_page_config(self.keyword_slug)
        
        if not self.config:
            raise Http404("Keyword page not found")
        
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        """Filter jobs based on keyword page configuration."""
        filter_type = self.config.get('filter_type', 'all')
        patterns = self.config.get('patterns', [])
        
        base_qs = visible_jobs().select_related(
            'organization', 'category'
        )
        
        if filter_type == 'all':
            # All jobs on the platform (all are impact jobs)
            return base_qs.order_by('-posted_at')

        elif filter_type == 'category':
            category_slug = self.config.get('category_slug')
            if category_slug:
                return base_qs.filter(category__slug=category_slug).order_by('-posted_at')
            return base_qs.none()

        elif filter_type == 'job_type':
            job_type = self.config.get('job_type')
            if job_type:
                return base_qs.filter(job_type=job_type).order_by('-posted_at')
            return base_qs.none()
        
        elif filter_type == 'nonprofit':
            # Filter by organization type or keywords
            q_objects = Q(organization__organization_type='nonprofit')
            for pattern in patterns:
                q_objects |= Q(organization__name__icontains=pattern)
                q_objects |= Q(title__icontains=pattern)
                q_objects |= Q(category__name__icontains=pattern)
            return base_qs.filter(q_objects).distinct().order_by('-posted_at')
        
        elif filter_type == 'keyword':
            # Filter by keyword patterns in title, description, or category
            q_objects = Q()
            for pattern in patterns:
                q_objects |= Q(title__icontains=pattern)
                q_objects |= Q(category__name__icontains=pattern)
                q_objects |= Q(organization__name__icontains=pattern)
            return base_qs.filter(q_objects).distinct().order_by('-posted_at')
        
        return base_qs.order_by('-posted_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        job_count = self.get_queryset().count()
        
        # Page metadata
        context['config'] = self.config
        context['job_count'] = job_count
        context['h1'] = self.config['h1']
        context['page_title'] = self.config['title'].format(count=job_count)
        context['meta_description'] = self.config['meta_desc'].format(count=job_count)
        context['intro'] = self.config['intro']
        context['icon'] = self.config['icon']
        context['keyword'] = self.config['keyword']
        
        # Get category breakdown
        jobs_qs = self.get_queryset()
        category_rows = (
            jobs_qs.values('category__name', 'category__slug')
            .exclude(category__name__isnull=True)
            .annotate(count=Count('id'))
            .order_by('-count')[:8]
        )
        context['category_breakdown'] = [
            (
                row['category__name'],
                {'slug': row['category__slug'], 'count': row['count']},
            )
            for row in category_rows
        ]
        
        # Salary stats
        jobs_with_salary = jobs_qs.exclude(salary_min__isnull=True)
        if jobs_with_salary.exists():
            from django.db.models import Avg, Min, Max
            salary_stats = jobs_with_salary.aggregate(
                avg_min=Avg('salary_min'),
                avg_max=Avg('salary_max'),
                lowest=Min('salary_min'),
                highest=Max('salary_max'),
            )
            context['salary_stats'] = {
                'avg_min': int(salary_stats['avg_min'] or 0),
                'avg_max': int(salary_stats['avg_max'] or 0),
                'lowest': int(salary_stats['lowest'] or 0),
                'highest': int(salary_stats['highest'] or 0),
                'count': jobs_with_salary.count(),
            }
        
        # Featured jobs (with salary, recent)
        context['featured_jobs'] = jobs_qs.exclude(
            salary_min__isnull=True
        ).order_by('-salary_max', '-posted_at')[:6]
        
        # Related keyword pages
        context['related_keywords'] = [
            page for page in KEYWORD_SEO_PAGES 
            if page['slug'] != self.keyword_slug
        ][:6]
        
        # Also show some related role pages
        context['related_roles'] = ROLE_SEO_PAGES[:4]
        
        # Categories for filter
        context['categories'] = Category.objects.all()
        
        # FAQs for structured data
        context['faqs'] = self._generate_faqs(job_count)
        
        return context
    
    def _generate_faqs(self, job_count):
        """Generate FAQ content for the keyword page."""
        keyword = self.config['keyword']
        return [
            {
                'question': f'How many {keyword} are available?',
                'answer': f'There are currently {job_count} {keyword} listed on Remote Impact. New positions are added daily from impact organizations worldwide.',
            },
            {
                'question': f'Are all {keyword} fully remote?',
                'answer': 'Yes, all jobs on Remote Impact are remote-friendly. Some may have geographic restrictions (e.g., US-only) or timezone preferences, which are clearly noted in job descriptions.',
            },
            {
                'question': f'What qualifications do I need for {keyword}?',
                'answer': 'Qualifications vary by role and organization. Many positions welcome career changers with transferable skills. Use our filters to find entry-level opportunities.',
            },
            {
                'question': f'How do I apply for {keyword}?',
                'answer': 'Click on any job listing to see full details and the application link. Most applications go directly to the hiring organization\'s website or ATS.',
            },
        ]


class AllKeywordPagesView(ListView):
    """
    Index page showing all keyword-based SEO pages.
    
    URL: /impact/ or /browse/
    """
    template_name = "jobs/seo/keyword_index.html"
    context_object_name = "keyword_pages"

    def get_queryset(self):
        """Get all keyword pages (no counts for speed)."""
        # Skip counting - just return the configs sorted by volume
        volume_order = {'high': 0, 'medium': 1, 'low': 2}
        result = sorted(
            KEYWORD_SEO_PAGES,
            key=lambda x: volume_order.get(x.get('search_volume', 'low'), 2)
        )
        return result

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = "Browse Remote Impact Jobs | Remote Impact"
        context['meta_description'] = "Explore remote impact job opportunities by category. Find nonprofit, humanitarian, environmental, and social impact careers from anywhere."
        context['total_jobs'] = visible_jobs().count()
        
        # Group by search volume for display
        high_volume = [p for p in context['keyword_pages'] if p.get('search_volume') == 'high']
        medium_volume = [p for p in context['keyword_pages'] if p.get('search_volume') == 'medium']
        low_volume = [p for p in context['keyword_pages'] if p.get('search_volume') == 'low']
        
        context['high_volume_pages'] = high_volume
        context['medium_volume_pages'] = medium_volume
        context['low_volume_pages'] = low_volume
        
        return context
