import json
import re
from html import unescape
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

from jobs.models import Category, Job, Organization


@override_settings(SOCIALACCOUNT_PROVIDERS={provider: {"APP": {"client_id": "test", "secret": "test", "key": ""}} for provider in ("google", "linkedin")})
class CategorySeoEvidenceTests(TestCase):
    def setUp(self):
        provider = patch('allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_provider',
                         return_value=Mock(get_login_url=Mock(return_value='/test-login/')))
        provider.start()
        self.addCleanup(provider.stop)
        self.category = Category.objects.create(name='Climate "Research" & Impact </script>', slug='climate-environment')
        self.other_category = Category.objects.create(name='Health', slug='global-health')
        self.org = Organization.objects.create(name='Impact Employer', slug='impact-employer')
        for i in range(3):
            Job.objects.create(title=f'Software Engineer {i}', slug=f'engineer-{i}',
                organization=self.org, category=self.category, source='manual')
        Job.objects.create(title='Operations Director', slug='ops', organization=self.org,
            category=self.category, source='manual')
        Job.objects.create(title='Software Engineer in Health', slug='health-engineer', organization=self.org,
            category=self.other_category, source='manual')
        self.url = reverse('jobs:category_landing', args=[self.category.slug])

    def test_structured_data_parses_and_matches_visible_faq_without_script_injection(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
        data = [entry for block in blocks for entry in (lambda d:d if isinstance(d,list) else [d])(json.loads(block))]
        faq = next(x for x in data if x.get('@type')=='FAQPage')
        for question in faq['mainEntity']:
            self.assertIn(question['name'], unescape(body))
            self.assertIn(question['acceptedAnswer']['text'], unescape(body))
        self.assertNotIn('Yes, all jobs on Remote Impact', body)
        self.assertNotIn("checked daily against each employer's own", body)
        self.assertEqual(response.context['job_count'], 4)
        self.assertEqual(response.context['inventory_evidence']['employers'], 1)
        self.assertContains(response, 'Current hiring in this search')
        self.assertContains(response, 'climate-environment-careers')
        self.assertEqual(next(x for x in data if x.get('@type')=='BreadcrumbList')['itemListElement'][-1]['name'],self.category.name)

    def test_role_link_query_filters_inside_category_and_stays_nonindexable(self):
        response = self.client.get(self.url, {'q':'software engineer','category':self.other_category.slug})
        self.assertEqual(response.context['job_count'], 3)
        self.assertEqual({j.slug for j in response.context['jobs']}, {'engineer-0','engineer-1','engineer-2'})
        self.assertContains(response, 'noindex, follow')
        self.assertContains(response, f'https://remoteimpact.org{self.url}')
        self.assertNotContains(response, 'Software Engineer in Health')
