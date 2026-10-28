"""
Programmatic SEO Views

Handles dynamic SEO landing pages for role-based searches:
- /remote-software-engineer-jobs/
- /remote-data-analyst-jobs/
- etc.
"""

from django.conf import settings
from django.utils.decorators import method_decorator
from ..services.page_cache import cache_public_page as cache_page
from .mixins import FastJobPaginationMixin
from django.views.generic import ListView
from django.http import Http404, HttpResponsePermanentRedirect
from django.db.models import Count, Max, Q
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from ..models import Job, Category, Organization
from ..seo_config import (
    MIN_INDEXABLE_JOBS,
    get_role_page_config, get_all_role_slugs, ROLE_SEO_PAGES,
    get_keyword_page_config, get_all_keyword_slugs, KEYWORD_SEO_PAGES,
)



def comparable_salary_jobs(jobs_qs):
    """Annual USD salaries in a plausible range, so stats never mix currencies.

    Without this, a rupee or hourly figure counted as dollars ("Top salary
    $4,575,615") and skewed every average on the page.
    """
    from django.db.models import Q
    from jobs.services.job_service import JobService
    return JobService._annual_salary_jobs(
        jobs_qs.filter(salary_min__isnull=False)
        .filter(Q(salary_currency__iexact="USD"))
        .filter(salary_min__gte=15000, salary_min__lte=600000)
        .filter(Q(salary_max__isnull=True) | Q(salary_max__lte=800000))
    )

def highest_advertised_jobs(jobs_qs):
    from jobs.services.job_service import JobService
    return JobService._order_jobs(comparable_salary_jobs(jobs_qs), 'salary-high')[:6]


def salary_summary(jobs_qs):
    """Compute the visible salary summary with one aggregate query.

    The count is part of the same aggregate, so role and keyword pages do not
    separately issue an existence query and a count over the same filtered set.
    """
    from django.db.models import Avg, Min, Max
    stats = comparable_salary_jobs(jobs_qs).aggregate(
        avg_min=Avg('salary_min'),
        avg_max=Avg('salary_max'),
        lowest=Min('salary_min'),
        highest=Max('salary_max'),
        count=Count('pk'),
    )
    if not stats['count']:
        return None
    return {
        'avg_min': int(stats['avg_min'] or 0),
        'avg_max': int(stats['avg_max'] or 0),
        'lowest': int(stats['lowest'] or 0),
        'highest': int(stats['highest'] or 0),
        'count': stats['count'],
    }


def visible_jobs():
    return Job.objects.filter(Job.availability_filter())


def nonprofit_inventory(base_qs):
    unclassified = Q(organization__organization_type='')
    source_nonprofit = Q(source=Job.Source.IDEALIST, raw_data__orgType='NONPROFIT')
    source_unknown = (~Q(source=Job.Source.IDEALIST) | Q(raw_data__orgType__isnull=True)
                      | Q(raw_data__orgType=None) | Q(raw_data__orgType=''))
    named_candidate = source_unknown & Q(
        organization__name__iregex=r'(^|[^a-z0-9_])(non[ -]?profit|ngo|charit(y|ies)|foundation)([^a-z0-9_]|$)')
    return base_qs.filter(
        Q(organization__organization_type__in=['nonprofit', 'foundation'])
        | (unclassified & (source_nonprofit | named_candidate)))


GUIDES_BY_CATEGORY = {
    'climate-environment': 'climate-environment-careers',
    'ai-safety': 'ai-safety-careers',
    'global-health': 'global-health-careers',
    'humanitarian-ingo': 'humanitarian-ingo-careers',
    'effective-altruism': 'effective-altruism-careers',
    'education': 'education-edtech-careers',
    'technology': 'tech-for-good-careers',
}


def inventory_evidence(queryset, categories):
    """Useful live evidence from the same inventory displayed on the page."""
    now = timezone.now()
    counts = queryset.aggregate(
        employers=Count('organization_id', distinct=True),
        new_week=Count('pk', filter=Q(posted_at__gte=now - timedelta(days=7))),
        last_source_sighting=Max('last_seen_at'),
    )
    hubs = []
    for name, info in categories:
        hub = {'name': name, 'count': info['count'],
               'url': reverse('jobs:category_landing', args=[info['slug']])}
        guide = GUIDES_BY_CATEGORY.get(info['slug'])
        if guide:
            hub['guide_url'] = reverse('jobs:pillar_detail', args=[guide])
        hubs.append(hub)
    return {'inventory_evidence': counts, 'inventory_hubs': hubs, 'inventory_date': now}


@method_decorator(cache_page(getattr(settings, 'CACHE_TTL_MEDIUM', 300)), name='dispatch')
class RoleJobsView(FastJobPaginationMixin, ListView):
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

    def _role_queryset(self):
        """Return this request's role membership while retaining live availability.

        The page uses the same role set for its paginator, category breakdown,
        inventory facts, salary summary and featured listings.  Holding only IDs
        avoids re-running the expensive title-pattern search for each section;
        every subsequent query still applies ``visible_jobs()`` so a listing
        that closes during the request cannot be displayed.
        """
        if not hasattr(self, "_role_job_ids"):
            self._role_job_ids = list(
                visible_jobs().filter(self._role_query()).order_by()
                .values_list("pk", flat=True)
            )
        return visible_jobs().filter(pk__in=self._role_job_ids)

    def _role_query(self):
        """Build the configured title-matching query once for this request."""
        if hasattr(self, "_role_query_filter"):
            return self._role_query_filter
        patterns = self.config['patterns']
        q_objects = Q()
        for pattern in patterns:
            q_objects |= Q(title__icontains=pattern)
        self._role_query_filter = q_objects
        return q_objects

    def get_queryset(self):
        """Filter live jobs matching the role patterns."""
        return self._role_queryset().select_related(
            'organization', 'category'
        ).order_by('-posted_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        job_count = context['page_obj'].paginator.count
        
        # Page metadata
        context['config'] = self.config
        context['job_count'] = job_count
        context['index_page'] = job_count >= MIN_INDEXABLE_JOBS and not self.request.GET
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
        context.update(inventory_evidence(jobs_qs, context['category_breakdown']))
        
        # Salary stats
        salary_stats = salary_summary(jobs_qs)
        if salary_stats:
            context['salary_stats'] = salary_stats
        
        # Featured jobs (with salary, recent)
        context['featured_jobs'] = highest_advertised_jobs(jobs_qs)
        
        # Related role pages
        context['related_roles'] = [
            page for page in ROLE_SEO_PAGES 
            if page['slug'] != self.role_slug
        ][:6]
        
        # Categories for filter
        context['categories'] = Category.objects.all()
        return context


@method_decorator(cache_page(300), name="dispatch")
class AllRolePagesView(ListView):
    """
    Index page showing all role-based SEO pages.
    
    URL: /remote-jobs/ or /careers/
    """
    template_name = "jobs/seo/role_index.html"
    context_object_name = "role_pages"

    def get_queryset(self):
        """Get all role pages with job counts."""
        from ..services.search_destinations import role_query
        result = []
        counters = {'total': Count('pk')}
        for index, config in enumerate(ROLE_SEO_PAGES):
            q_objects = role_query(config['slug'])
            if q_objects is None:
                q_objects = Q()
                for pattern in config['patterns']:
                    q_objects |= Q(title__icontains=pattern)
            
            counters[f'role_{index}'] = Count('pk', filter=q_objects)
        counts = visible_jobs().aggregate(**counters)
        self.total_jobs = counts['total']
        for index, config in enumerate(ROLE_SEO_PAGES):
            count = counts[f'role_{index}']
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
        context['total_jobs'] = self.total_jobs
        return context


# =============================================================================
# KEYWORD SEO PAGES
# Target long-tail keywords from keyword research
# =============================================================================

@method_decorator(cache_page(getattr(settings, 'CACHE_TTL_MEDIUM', 300)), name='dispatch')
class KeywordJobsView(FastJobPaginationMixin, ListView):
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
        from ..services.seo_identity import IMPACT_PAGE_ALIASES, IMPACT_CANONICAL_SLUG
        if self.keyword_slug in IMPACT_PAGE_ALIASES:
            destination = reverse('jobs:keyword_jobs', args=[IMPACT_CANONICAL_SLUG])
            query = request.GET.urlencode()
            return HttpResponsePermanentRedirect(destination + ('?' + query if query else ''))
        if self.keyword_slug == 'remote-climate-jobs':
            destination = reverse('jobs:category_landing', args=['climate-environment'])
            query = request.GET.urlencode()
            return HttpResponsePermanentRedirect(destination + ('?' + query if query else ''))
        if self.keyword_slug == 'remote-positions-in-nonprofits':
            destination = reverse('jobs:keyword_jobs', args=['remote-nonprofit-jobs'])
            query = request.GET.urlencode()
            return HttpResponsePermanentRedirect(destination + ('?' + query if query else ''))
        self.config = get_keyword_page_config(self.keyword_slug)
        
        if not self.config:
            raise Http404("Keyword page not found")
        
        return super().dispatch(request, *args, **kwargs)

    def _nonprofit_queryset(self, base_qs):
        # Keep one request-local membership snapshot. Re-running the raw JSON /
        # employer-name classifier for every aggregate repeatedly scans source
        # records; availability and user filters still apply to every query.
        if not hasattr(self, '_nonprofit_job_ids'):
            self._nonprofit_job_ids = list(
                nonprofit_inventory(base_qs).order_by().values_list('pk', flat=True)
            )
        return base_qs.filter(pk__in=self._nonprofit_job_ids)

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
            # Employer identity is stronger evidence than words in a job title.
            # Unclassified employers with nonprofit-related names remain candidates,
            # explicitly explained on the page; known other types never qualify by name.
            queryset = self._nonprofit_queryset(base_qs)
            query = self.request.GET.get('q', '').strip()[:100] if hasattr(self, 'request') else ''
            if query:
                queryset = queryset.filter(title__icontains=query)
            country = self.request.GET.get('country', '').strip()[:100] if hasattr(self, 'request') else ''
            if country:
                queryset = queryset.filter(country__iexact=country)
            experience = self.request.GET.get('experience', '') if hasattr(self, 'request') else ''
            if experience in {'entry', 'internship'}:
                queryset = queryset.filter(experience_level=experience)
            return queryset.order_by('-posted_at')

        elif filter_type == 'keyword':
            # Filter by keyword patterns in title, description, or category
            q_objects = Q()
            for pattern in patterns:
                q_objects |= Q(title__icontains=pattern)
                q_objects |= Q(category__name__icontains=pattern)
                q_objects |= Q(organization__name__icontains=pattern)
            if self.keyword_slug == 'remote-humanitarian-jobs':
                # Specific aid missions in a description can make a generic
                # finance or technical role relevant. On PostgreSQL use the
                # existing index to bound the exact-text check.
                from django.db import connection
                phrases = ('humanitarian response', 'humanitarian aid',
                           'humanitarian assistance', 'disaster relief',
                           'refugee response', 'international development')
                literal = Q()
                for phrase in phrases:
                    literal |= Q(description__icontains=phrase)
                if connection.vendor == 'postgresql':
                    from django.contrib.postgres.search import SearchQuery
                    indexed = Q()
                    for phrase in phrases:
                        indexed |= Q(search_vector=SearchQuery(phrase, config='english', search_type='phrase'))
                    matches = base_qs.filter(indexed).filter(literal).values('pk')
                    unindexed = base_qs.filter(search_vector__isnull=True).filter(literal).values('pk')
                    q_objects |= Q(pk__in=matches) | Q(pk__in=unindexed)
                else:
                    q_objects |= literal
            return base_qs.filter(q_objects).distinct().order_by('-posted_at')
        
        return base_qs.order_by('-posted_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        job_count = context['page_obj'].paginator.count
        
        # Page metadata
        context['config'] = self.config
        context['job_count'] = job_count
        context['index_page'] = job_count >= MIN_INDEXABLE_JOBS and not self.request.GET
        context['h1'] = self.config['h1']
        context['page_title'] = self.config['title'].format(count=job_count)
        context['meta_description'] = self.config['meta_desc'].format(count=job_count)
        context['intro'] = self.config['intro']
        context['icon'] = self.config['icon']
        context['keyword'] = self.config['keyword']
        context['nonprofit_page'] = self.config.get('filter_type') == 'nonprofit'
        context['role_query'] = self.request.GET.get('q', '').strip()[:100]
        context['location_query'] = self.request.GET.get('country', '').strip()[:100]
        experience = self.request.GET.get('experience', '')
        context['experience_query'] = experience if experience in {'entry', 'internship'} else ''
        if context['nonprofit_page']:
            context['nonprofit_locations'] = list(
                self._nonprofit_queryset(visible_jobs()).exclude(country__isnull=True).exclude(country='')
                .order_by('country').values_list('country', flat=True).distinct()
            )
            if context['location_query'] and context['location_query'].casefold() not in {
                    value.casefold() for value in context['nonprofit_locations']}:
                context['nonprofit_locations'].append(context['location_query'])
        
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
        context.update(inventory_evidence(jobs_qs, context['category_breakdown']))
        if context['nonprofit_page'] and not context['role_query']:
            choices = [('development', 'Development'), ('grant', 'Grants'),
                       ('program', 'Programs'), ('operations', 'Operations'),
                       ('communications', 'Communications'), ('finance', 'Finance'),
                       ('fundrais', 'Fundraising')]
            counts = jobs_qs.aggregate(**{term: Count('pk', filter=Q(title__icontains=term))
                                         for term, label in choices})
            context['nonprofit_roles'] = [{'query': term, 'label': label, 'count': counts[term]}
                                         for term, label in choices if counts[term]]
        
        # Salary stats
        salary_stats = salary_summary(jobs_qs)
        if salary_stats:
            context['salary_stats'] = salary_stats
        
        # Featured jobs (with salary, recent)
        context['featured_jobs'] = highest_advertised_jobs(jobs_qs)
        
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
        if context['nonprofit_page']:
            context['faqs'] = [
                {'question': 'Which employers appear in this nonprofit jobs search?',
                 'answer': 'We include employers recorded as nonprofit or foundation, employers classified as nonprofit in their Idealist source listing, and unclassified employers whose names explicitly mention nonprofit, NGO, charity or foundation. Source classifications and name matches do not independently verify legal nonprofit status. Known other employer types are excluded from the source/name fallback. A job title alone does not qualify an employer.'},
                {'question': 'Can I apply from any country?',
                 'answer': 'Remote does not mean worldwide. Some employers hire only in specified countries or time zones, and some roles require travel. Read the location and original employer posting before applying.'},
                {'question': 'How should I compare nonprofit salaries?',
                 'answer': 'The salary summary covers only listings with comparable annual USD pay. Missing pay is unknown, not unpaid; hourly and other-currency figures are excluded from that summary.'},
                {'question': 'How can I find entry-level remote nonprofit jobs?',
                 'answer': 'Choose Entry-level in the experience filter to see listings recorded at that level within this nonprofit search. Entry-level does not necessarily mean no experience or no degree; check the employer requirements. For supported first steps, explore our separate fellowships and internships directory.'},
                {'question': 'How do I apply for a remote nonprofit job?',
                 'answer': 'Open a listing to review its requirements and follow the application link to the employer or its recruiting platform. Check that the role is still open on the original posting.'},
            ]
        
        return context
    
    def _generate_faqs(self, job_count):
        """Generate FAQ content for the keyword page."""
        keyword = self.config['keyword']
        return [
            {
                'question': f'How many {keyword} are available?',
                'answer': f'There are currently {job_count} {keyword} listed on Remote Impact. New listings are imported from job boards and employer career pages several times a day.',
            },
            {
                'question': f'Are all {keyword} fully remote?',
                'answer': 'Every listing is remote, but many are limited to certain countries or time zones. Check the location line and the original posting before you apply.',
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


# Extend the established destinations without changing unrelated role searches.
from ..services.search_destinations import SearchDestinationRoleMixin, SearchDestinationKeywordMixin

_BaseRoleJobsView = RoleJobsView


class RoleJobsView(SearchDestinationRoleMixin, _BaseRoleJobsView):
    pass


_BaseKeywordJobsView = KeywordJobsView


class KeywordJobsView(SearchDestinationKeywordMixin, _BaseKeywordJobsView):
    pass
