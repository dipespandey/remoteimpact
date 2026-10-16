"""Bounded landing-page discovery; counts never establish applicant eligibility."""
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.urls import reverse

DESTINATIONS = {
    'remote-education-jobs': {
        'key': 'education', 'label': 'education',
        'prompt': 'Remote education roles in teaching, curriculum, instructional design or student support. Show hiring locations and disclosed pay.',
        'roles': [('teach', 'Teaching'), ('curricul', 'Curriculum'), ('instructional', 'Instructional design'), ('student support', 'Student support'), ('training', 'Training')],
    },
    'remote-sustainability-jobs': {
        'key': 'sustainability', 'label': 'sustainability',
        'prompt': 'Remote sustainability roles in climate, ESG, carbon or environmental programs. Show hiring locations and disclosed pay.',
        'roles': [('sustainab', 'Sustainability'), ('climate', 'Climate'), ('carbon', 'Carbon'), ('environment', 'Environmental'), ('esg', 'ESG')],
    },
    'remote-nonprofit-jobs': {
        'key': 'nonprofit', 'label': 'nonprofit',
        'prompt': 'Remote roles at nonprofits or foundations. Show hiring locations, responsibilities and disclosed pay.',
        'roles': [],
    },
}

# A bare "learning" also matches machine learning; a bare "student" includes
# fellowships for students and unrelated working-student jobs. Require an
# education responsibility rather than either substring.
EDUCATION_TITLE = (r'(^|[^a-z0-9_])(educat(ion(al)?|or)s?|teach(er|ing)?s?|tutor(ing)?s?|'
    r'curricul(um|a)|instructional design(ers?)?|learning (design(ers?)?|'
    r'and development|& development|experience|specialist|content|strategy|technology)|student '
    r'(support|success|services|affairs|advis(or|er))|academic (programs?|advis(or|er)|affairs|director|coordinator|support|lead|manager)|instructor|'
    r'professor|lecturer|training)([^a-z0-9_]|$)')


def role_query(slug):
    if slug == 'remote-education-jobs':
        return Q(title__iregex=EDUCATION_TITLE) & ~Q(title__iregex=(
            r'(^|[^a-z0-9_])((ai|llm|model|machine) (tutor|trainer)|pre[- ]?training|post[- ]?training|paid training|training provided)([^a-z0-9_]|$)'))
    if slug == 'remote-sustainability-jobs':
        return Q(title__iregex=r'(^|[^a-z0-9_])(sustainab\w*|environment\w*|climate|carbon|renewable energy|clean energy|ESG|decarboni[sz]\w*)([^a-z0-9_]|$)')
    return None


def filtered(queryset, request, slug):
    if slug not in DESTINATIONS:
        return queryset
    query = request.GET.get('q', '').strip()[:100]
    country = request.GET.get('country', '').strip()[:100]
    if query:
        queryset = queryset.filter(title__icontains=query)
    if country:
        queryset = queryset.filter(country__iexact=country)
    return queryset


def page_context(slug, request, inventory):
    config = DESTINATIONS.get(slug)
    if not config:
        return {}
    query = request.GET.get('q', '').strip()[:100]
    country = request.GET.get('country', '').strip()[:100]
    params = {key: value for key, value in [('q', query), ('country', country)] if value}
    roles = []
    if config['roles']:
        scope = inventory.filter(country__iexact=country) if country else inventory
        counts = scope.aggregate(**{f'role_{i}': Count('pk', filter=Q(title__icontains=term))
                                   for i, (term, _) in enumerate(config['roles'])})
        roles = [{'label': label, 'count': counts[f'role_{i}'],
                  'url': '?' + urlencode({**({'country': country} if country else {}), 'q': term})}
                 for i, (term, label) in enumerate(config['roles']) if counts[f'role_{i}']]
    locations = list(inventory.exclude(country__isnull=True).exclude(country='').order_by('country').values_list('country', flat=True).distinct()) if config['roles'] else []
    if country and country.casefold() not in {value.casefold() for value in locations}:
        locations.append(country)
    return {
        'search_destination': config,
        'destination_query': query, 'destination_country': country,
        'destination_locations': locations, 'destination_roles': roles,
        'destination_pagination_query': '&' + urlencode(params) if params else '',
        'destination_scout_url': reverse('jobs:job_scout_preview') + '?' + urlencode({
            'intent': config['prompt'], 'source': 'seo_' + config['key']}),
    }


def salary_sample(inventory):
    from django.db.models import Avg
    from jobs.views.seo_pages import comparable_salary_jobs
    stats = comparable_salary_jobs(inventory).aggregate(
        avg_min=Avg('salary_min'), avg_max=Avg('salary_max'),
        count=Count('pk'), max_count=Count('salary_max'))
    if not stats['count']:
        return None
    return {key: int(value or 0) for key, value in stats.items()}


class SearchDestinationRoleMixin:
    def _destination_inventory(self):
        from jobs.models import Job
        query = role_query(self.role_slug)
        if not hasattr(self, '_destination_ids'):
            self._destination_ids = list(Job.objects.filter(Job.availability_filter()).filter(query).order_by().values_list('pk', flat=True))
        return Job.objects.filter(Job.availability_filter(), pk__in=self._destination_ids)

    def get_queryset(self):
        if getattr(self, 'role_slug', None) not in ('remote-education-jobs', 'remote-sustainability-jobs'):
            return super().get_queryset()
        return filtered(self._destination_inventory(), self.request, self.role_slug).select_related('organization', 'category').order_by('-posted_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.role_slug not in ('remote-education-jobs', 'remote-sustainability-jobs'):
            return context
        context.update(page_context(self.role_slug, self.request, self._destination_inventory()))
        context['salary_stats'] = salary_sample(self.get_queryset())
        if self.role_slug == 'remote-education-jobs':
            context.update(
                page_title='Remote Education Jobs | Teaching & Learning Design',
                meta_description='Browse remote teaching, curriculum, instructional design and student support jobs. Check hiring locations, disclosed pay and original employer postings.',
                intro='Browse remote teaching, curriculum, instructional design, training and student support roles. Hiring countries, qualifications and working hours vary by employer.')
        return context


class SearchDestinationKeywordMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.keyword_slug == 'remote-nonprofit-jobs':
            context.update(page_context(self.keyword_slug, self.request, self.get_queryset()))
            context['salary_stats'] = salary_sample(self.get_queryset())
        return context
