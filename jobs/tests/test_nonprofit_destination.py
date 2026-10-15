import json
import re
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

from jobs.models import Job, Organization
from jobs.seo_config import KEYWORD_SEO_PAGES


@override_settings(SOCIALACCOUNT_PROVIDERS={p: {'APP': {'client_id': 'test', 'secret': 'test', 'key': ''}} for p in ('google', 'linkedin')})
class NonprofitDestinationTests(TestCase):
    def setUp(self):
        for mocked in [patch('jobs.signals.threading.Thread'),
                       patch('jobs.services.embedding_service.embed_job', return_value=[0.0] * 384),
                       patch('allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_provider', return_value=Mock(get_login_url=Mock(return_value='/test-login/')))]:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.url = reverse('jobs:keyword_jobs', args=['remote-nonprofit-jobs'])

    def job(self, slug, name, kind='', title='Program Manager', **kwargs):
        org = Organization.objects.create(name=name, slug=slug, organization_type=kind)
        return Job.objects.create(title=title, slug=slug, organization=org, source='manual', **kwargs)

    def test_employer_evidence_and_availability_define_membership(self):
        expected = [self.job('typed', 'Community Support', 'nonprofit'),
                    self.job('foundation', 'Community Support Fund', 'foundation'),
                    self.job('named', 'Example Charity')]
        self.job('company', 'Foundation Software', 'bcorp')
        self.job('title-only', 'Software Vendor', title='Nonprofit Account Executive')
        self.job('substring', 'Mango Software')
        self.job('closed', 'Closed Charity', is_active=False)
        response = self.client.get(self.url)
        self.assertEqual(response.context['job_count'], 3)
        self.assertEqual({j.pk for j in response.context['jobs']}, {j.pk for j in expected})
        self.assertContains(response, 'legal nonprofit status is not verified for every employer')

    def test_role_filter_stays_within_nonprofit_inventory_and_is_noindex(self):
        job = self.job('fundraiser', 'Public Giving', 'nonprofit', title='Fundraising Manager')
        self.job('program', 'Example Charity')
        self.job('commercial', 'Commercial Software', 'bcorp', title='Fundraising Manager')
        response = self.client.get(self.url, {'q': 'fundrais'})
        self.assertEqual([j.pk for j in response.context['jobs']], [job.pk])
        self.assertContains(response, 'name="robots" content="noindex, follow"')
        self.assertContains(response, f'https://remoteimpact.org{self.url}')

    def test_old_duplicate_redirects_preserving_search_and_pagination(self):
        response = self.client.get('/impact/remote-positions-in-nonprofits/?q=program&page=2')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self.url + '?q=program&page=2')
        self.assertNotIn('remote-positions-in-nonprofits', [c['slug'] for c in KEYWORD_SEO_PAGES])

    def test_home_and_board_link_to_primary_destination_with_distinct_titles(self):
        home = self.client.get('/')
        board = self.client.get('/jobs/')
        self.assertContains(home, self.url)
        self.assertContains(board, self.url)
        self.assertContains(home, '<title>Remote Impact | Remote Jobs with Social Impact</title>', html=True)
        self.assertContains(board, '<title>Impact Jobs: Browse Remote Roles | Remote Impact</title>', html=True)

    def test_faq_schema_is_parseable_and_matches_visible_answers(self):
        self.job('ngo', 'Example NGO')
        response = self.client.get(self.url)
        body = response.content.decode()
        blocks = [json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)]
        faq = next(b for b in blocks if b.get('@type') == 'FAQPage')
        for item in faq['mainEntity']:
            self.assertContains(response, item['name'])
            self.assertContains(response, item['acceptedAnswer']['text'])
        self.assertContains(response, 'Remote does not mean worldwide')
        self.assertContains(response, 'Missing pay is unknown, not unpaid')

    def test_filtered_pagination_preserves_role_search(self):
        org = Organization.objects.create(name='Example Charity', slug='pagination')
        Job.objects.bulk_create([Job(title='Program Manager', slug=f'program-{i}',
            organization=org, source='manual') for i in range(26)])
        response = self.client.get(self.url, {'q': 'Program'})
        self.assertContains(response, '?page=2&amp;q=Program')
        second = self.client.get(self.url, {'q': 'Program', 'page': '2'})
        self.assertEqual(len(second.context['jobs']), 1)
        self.assertContains(second, '?page=1&amp;q=Program')
