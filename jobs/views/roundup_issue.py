from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from jobs.services.roundup_issue import issue
from jobs.services.seo_text import json_ld


@never_cache
@require_safe
def roundup_issue(request, issue_slug):
    selection = issue(issue_slug)
    if selection is None:
        raise Http404('Unknown roundup issue')
    canonical = settings.SITE_URL.rstrip('/') + reverse('jobs:roundup_issue', args=[issue_slug])
    schema = {'@context': 'https://schema.org', '@type': 'CollectionPage',
              'name': selection['title'], 'description': selection['introduction'],
              'url': canonical, 'datePublished': selection['published_on']}
    return render(request, 'jobs/newsletter/issue.html',
                  {'issue': selection, 'canonical': canonical, 'schema_json': json_ld(schema)})
