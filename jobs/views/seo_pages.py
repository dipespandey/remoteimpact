"""
Programmatic SEO Views

Handles dynamic SEO landing pages for role-based searches:
- /remote-software-engineer-jobs/
- /remote-data-analyst-jobs/
- etc.
"""

from django.views.generic import ListView
from django.http import Http404
from django.db.models import Q
from django.utils import timezone

from ..models import Job, Category
from ..seo_config import get_role_page_config, get_all_role_slugs, ROLE_SEO_PAGES


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
        
        return Job.objects.filter(
            is_active=True
        ).filter(q_objects).select_related(
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
        
        # Get category breakdown for this role
        category_counts = {}
        for job in self.get_queryset().values('category__name', 'category__slug').distinct():
            cat_name = job['category__name']
            cat_slug = job['category__slug']
            if cat_name:
                count = self.get_queryset().filter(category__slug=cat_slug).count()
                category_counts[cat_name] = {'slug': cat_slug, 'count': count}
        
        context['category_breakdown'] = sorted(
            category_counts.items(), 
            key=lambda x: x[1]['count'], 
            reverse=True
        )[:8]
        
        # Salary stats
        jobs_with_salary = self.get_queryset().exclude(salary_min__isnull=True)
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
        context['featured_jobs'] = self.get_queryset().exclude(
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
            
            count = Job.objects.filter(is_active=True).filter(q_objects).count()
            
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
        context['total_jobs'] = Job.objects.filter(is_active=True).count()
        return context
