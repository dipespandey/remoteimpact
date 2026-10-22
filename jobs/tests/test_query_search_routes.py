import json
import re
from datetime import date
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from django.urls import reverse
from jobs.models import Category, Job, Organization
from jobs.services.early_careers import opportunities


@override_settings(SOCIALACCOUNT_PROVIDERS={p: {'APP': {'client_id': 'test', 'secret': 'test', 'key': ''}} for p in ('google', 'linkedin')})
class QuerySearchRoutesTests(TestCase):
    def setUp(self):
        for mocked in [patch('jobs.signals.threading.Thread'), patch('jobs.services.embedding_service.embed_job', return_value=[0.0]*384), patch('allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_provider', return_value=Mock(get_login_url=Mock(return_value='/test-login/')))]:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.org = Organization.objects.create(name='Community Foundation', slug='community-foundation', organization_type='nonprofit')
        self.categories = {slug: Category.objects.create(name=name, slug=slug) for slug, name in [('climate-environment', 'Climate'), ('global-health', 'Global Health'), ('ai-safety', 'AI Safety')]}

    def job(self, slug, **kwargs):
        return Job.objects.create(slug=slug, title=kwargs.pop('title', 'Policy Associate'), organization=kwargs.pop('organization', self.org), source='manual', **kwargs)

    def test_category_routes_use_real_filters_and_visible_faq_matches_schema(self):
        for slug, category in self.categories.items():
            with self.subTest(slug=slug):
                self.job(slug+'-entry', category=category, experience_level='entry', country='Europe')
                response = self.client.get(reverse('jobs:category_landing', args=[slug]))
                profile = response.context['search_guidance']
                self.assertContains(response, profile['heading'])
                self.assertContains(response, profile['answer'])
                blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', response.content.decode(), re.S)
                data = [x for block in blocks for x in (lambda d: d if isinstance(d, list) else [d])(json.loads(block))]
                faq = next(x for x in data if x.get('@type') == 'FAQPage')
                self.assertTrue(any(q['acceptedAnswer']['text'] == profile['answer'] for q in faq['mainEntity']))
                for link in profile['links']:
                    filtered = self.client.get(link['url'])
                    self.assertEqual(filtered.status_code, 200)
                    self.assertContains(filtered, 'noindex, follow')
                    self.assertTrue(all(j.category_id == category.pk for j in filtered.context['jobs']))

    def test_category_europe_search_excludes_other_regions_and_closed_roles(self):
        category = self.categories['climate-environment']
        expected = self.job('eu-entry', category=category, country='Europe', experience_level='entry')
        self.job('us-entry', category=category, country='USA', experience_level='entry')
        self.job('eu-closed', category=category, country='Europe', is_active=False)
        url = reverse('jobs:category_landing', args=[category.slug])
        response = self.client.get(url, {'country': 'Europe', 'experience': 'entry'})
        self.assertEqual([j.pk for j in response.context['jobs']], [expected.pk])
        self.assertContains(response, f'https://remoteimpact.org{url}')
        self.assertContains(response, 'do not establish that every country')

    def test_nonprofit_experience_intersects_membership_role_location_and_pagination(self):
        for i in range(26):
            self.job(f'junior-{i}', title='Grants Assistant', country='Germany', experience_level='entry')
        self.job('senior', title='Grants Director', country='Germany', experience_level='senior')
        self.job('other-place', title='Grants Assistant', country='USA', experience_level='entry')
        self.job('closed', title='Grants Assistant', country='Germany', experience_level='entry', is_active=False)
        company = Organization.objects.create(name='Foundation Software', slug='company', organization_type='bcorp')
        self.job('company-role', title='Grants Assistant', country='Germany', experience_level='entry', organization=company)
        url = reverse('jobs:keyword_jobs', args=['remote-nonprofit-jobs'])
        query = {'q': 'Grants', 'country': 'Germany', 'experience': 'entry'}
        response = self.client.get(url, query)
        self.assertEqual(response.context['job_count'], 26)
        self.assertContains(response, 'value="entry" selected')
        self.assertContains(response, '?page=2&amp;q=Grants&amp;country=Germany&amp;experience=entry')
        second = self.client.get(url, {**query, 'page': 2})
        self.assertEqual(len(second.context['jobs']), 1)
        self.assertContains(second, 'noindex, follow')
        self.assertTrue(all(j.organization_id == self.org.pk and j.experience_level == 'entry' for j in response.context['jobs']))

    def test_paid_remote_internship_count_and_filter_exclude_unpaid_travel_closed_and_fellowships(self):
        prepared = opportunities(date(2026, 10, 19))
        rows = []
        for i, overrides in enumerate([{}, {'funding': 'unpaid'}, {'mode': 'hybrid'}, {'status': 'closed'}, {'kind': 'fellowship'}]):
            rows.append(dict(prepared[i], slug=f'programme-{i}', kind='internship', funding='paid', mode='remote', status='open'))
            rows[-1].update(overrides)
        url = reverse('jobs:early_careers')
        with patch('jobs.views.early_careers.opportunities', return_value=rows):
            response = self.client.get(url)
            self.assertEqual(response.context['open_paid_remote_internships'], 1)
            self.assertContains(response, '1 reviewed programme currently matches')
            filtered = self.client.get(url, {'kind': 'internship', 'funding': 'paid', 'mode': 'remote', 'status': 'open'})
            self.assertEqual(filtered.context['result_count'], 1)
            self.assertEqual([r['slug'] for g in filtered.context['groups'] for r in g['items']], ['programme-0'])
            self.assertContains(filtered, 'noindex, follow')
        with patch('jobs.views.early_careers.opportunities', return_value=rows[1:]):
            self.assertContains(self.client.get(url), 'No reviewed programmes currently match all three')
