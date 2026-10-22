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
        return Job.objects.create(title=title, slug=slug, organization=org, source=kwargs.pop('source', 'manual'), **kwargs)

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

    def test_source_classification_adds_nonprofits_but_never_other_known_types(self):
        included = self.job('source-nonprofit', 'Community Support', source='idealist',
                            raw_data={'orgType': 'NONPROFIT'})
        self.job('source-company', 'Community Foundation Software', source='idealist',
                 raw_data={'orgType': 'CORPORATION'})
        self.job('source-recruiter', 'Foundation Talent', source='idealist',
                 raw_data={'orgType': 'RECRUITER'})
        self.job('other-source', 'Other Company', source='climatebase',
                 raw_data={'orgType': 'NONPROFIT'})
        self.job('recorded-other', 'Example Foundation', 'bcorp', source='idealist',
                 raw_data={'orgType': 'NONPROFIT'})
        self.job('closed-source', 'Closed Community', source='idealist',
                 raw_data={'orgType': 'NONPROFIT'}, is_active=False)
        unknown = self.job('source-unknown', 'Example Charity', source='idealist',
                           raw_data={'orgType': None})
        response = self.client.get(self.url)
        self.assertEqual({j.pk for j in response.context['jobs']}, {included.pk, unknown.pk})
        self.assertContains(response, 'employers classified as nonprofit by Idealist')

    def test_role_filter_stays_within_nonprofit_inventory_and_is_noindex(self):
        job = self.job('fundraiser', 'Public Giving', 'nonprofit', title='Fundraising Manager')
        self.job('program', 'Example Charity')
        self.job('commercial', 'Commercial Software', 'bcorp', title='Fundraising Manager')
        response = self.client.get(self.url, {'q': 'fundrais'})
        self.assertEqual([j.pk for j in response.context['jobs']], [job.pk])
        self.assertContains(response, 'name="robots" content="noindex, follow"')
        self.assertContains(response, f'https://remoteimpact.org{self.url}')

    def test_location_label_filter_preserves_nonprofit_membership_and_role_search(self):
        expected = self.job('german-grants', 'Giving Charity', country='Germany', title='Grants Manager')
        self.job('us-grants', 'US Charity', country='USA', title='Grants Manager')
        self.job('worldwide-grants', 'Global Charity', country='Worldwide', title='Grants Manager')
        self.job('company-grants', 'Commercial Vendor', 'bcorp', country='Germany', title='Grants Manager')
        self.job('closed-grants', 'Closed Charity', country='Germany', title='Grants Manager', is_active=False)
        response = self.client.get(self.url, {'country': 'germany', 'q': 'Grants'})
        self.assertEqual([j.pk for j in response.context['jobs']], [expected.pk])
        self.assertContains(response, 'value="Germany" selected')
        self.assertContains(response, 'not verified applicant eligibility')
        self.assertContains(response, 'name="robots" content="noindex, follow"')
        self.assertIn('USA', response.context['nonprofit_locations'])

    def test_location_filter_survives_pagination_and_unknown_label_is_not_reset(self):
        org = Organization.objects.create(name='Location Charity', slug='location-pages')
        Job.objects.bulk_create([Job(title='Program Manager', slug=f'location-{i}', organization=org,
            source='manual', country='Germany') for i in range(26)])
        response = self.client.get(self.url, {'q': 'Program', 'country': 'Germany'})
        self.assertContains(response, '?page=2&amp;q=Program&amp;country=Germany')
        second = self.client.get(self.url, {'q': 'Program', 'country': 'Germany', 'page': '2'})
        self.assertEqual(len(second.context['jobs']), 1)
        empty = self.client.get(self.url, {'country': 'No matching location'})
        self.assertEqual(empty.context['job_count'], 0)
        self.assertContains(empty, 'value="No matching location" selected')

    def test_highest_pay_uses_known_bound_without_promoting_unknown_maximum(self):
        jobs = [self.job(f'pay-{i}', f'Pay Foundation {i}', salary_min=low, salary_max=high)
                for i, (low, high) in enumerate([(40000, None), (100000, 200000),
                    (300000, None), (100000, 180000), (100000, 160000),
                    (100000, 140000), (100000, 120000)])]
        self.job('foreign-pay', 'Foreign Foundation', salary_min=500000, salary_max=600000,
                 salary_currency='EUR')
        self.job('hourly-pay', 'Hourly Foundation', salary_min=500000, salary_max=600000,
                 raw_data={'salary_period': 'hour'})
        self.job('closed-pay', 'Closed Foundation', salary_min=500000, salary_max=600000,
                 is_active=False)
        expected = [jobs[i].pk for i in [2, 1, 3, 4, 5, 6]]
        for url in [self.url, reverse('jobs:role_jobs', args=['remote-program-manager-jobs'])]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual([j.pk for j in response.context['featured_jobs']], expected)
                self.assertContains(response, 'minimum when no maximum is given')

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

    def test_membership_snapshot_is_request_local_and_availability_remains_live(self):
        from django.test import RequestFactory
        from jobs.views.seo_pages import KeywordJobsView, visible_jobs
        included = self.job('snapshot', 'Community Support', source='idealist',
                            raw_data={'orgType': 'NONPROFIT'})
        first = KeywordJobsView()
        self.assertEqual(set(first._nonprofit_queryset(visible_jobs()).values_list('pk', flat=True)), {included.pk})
        # An inactive listing cannot survive through the request-local ID set.
        Job.objects.filter(pk=included.pk).update(is_active=False)
        self.assertFalse(first._nonprofit_queryset(visible_jobs()).exists())
        # A new view/request re-evaluates source classification rather than
        # retaining another request's membership or using a shared cache.
        Job.objects.filter(pk=included.pk).update(is_active=True, raw_data={'orgType': 'CORPORATION'})
        second = KeywordJobsView()
        self.assertFalse(second._nonprofit_queryset(visible_jobs()).exists())

    def test_role_membership_snapshot_is_request_local_and_availability_remains_live(self):
        from jobs.views.seo_pages import RoleJobsView, visible_jobs
        included = self.job('role-snapshot', 'Community Support', title='Program Manager')
        first = RoleJobsView()
        first.config = {'patterns': ['program manager']}
        self.assertEqual(set(first._role_queryset().values_list('pk', flat=True)), {included.pk})
        # The snapshot stores IDs, but every use re-applies availability.
        Job.objects.filter(pk=included.pk).update(is_active=False)
        self.assertFalse(first._role_queryset().exists())
        # A new view/request must rebuild membership rather than reuse another
        # request's values.
        Job.objects.filter(pk=included.pk).update(is_active=True, title='Operations Manager')
        second = RoleJobsView()
        second.config = {'patterns': ['program manager']}
        self.assertFalse(second._role_queryset().exists())

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

    def test_quick_role_links_only_offer_nonempty_searches(self):
        self.job('grants', 'Example Charity', title='Grants Manager')
        self.job('programs', 'Public Support', 'nonprofit', title='Program Director')
        response = self.client.get(self.url)
        self.assertEqual({r['query']: r['count'] for r in response.context['nonprofit_roles']},
                         {'grant': 1, 'program': 1})
        self.assertContains(response, 'Grants (1)')
        self.assertNotContains(response, 'href="?q=fundrais"')
