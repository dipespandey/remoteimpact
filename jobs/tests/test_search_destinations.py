import json
import re
from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from jobs.models import ConsumerFunnelEvent as E, Job, Organization


@override_settings(SECURE_SSL_REDIRECT=False, SOCIALACCOUNT_PROVIDERS={p: {'APP': {'client_id': 'test', 'secret': 'test', 'key': ''}} for p in ('google', 'linkedin')})
class SearchDestinationTests(TestCase):
    def setUp(self):
        for mocked in (patch('jobs.signals.threading.Thread'),
                       patch('jobs.services.embedding_service.embed_job', return_value=[0.0] * 384),
                       patch('allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_provider', return_value=Mock(get_login_url=Mock(return_value='/test-login/')))):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.org = Organization.objects.create(name='Education Charity', slug='education-fixture', organization_type='nonprofit')
        self.education = reverse('jobs:role_jobs', args=['remote-education-jobs'])
        self.sustainability = reverse('jobs:role_jobs', args=['remote-sustainability-jobs'])

    def job(self, title, **kwargs):
        return Job.objects.create(title=title, slug=f'fixture-{Job.objects.count()}', organization=self.org, source='manual', **kwargs)

    def test_education_requires_education_work_not_machine_learning_or_student_status(self):
        included = [self.job(title) for title in ['Teacher', 'Associate Instructional Designer', 'Training & Curriculum Lead', 'Student Support Advisor', 'Learning and Development Manager', 'Online Tutor']]
        for title in ['Machine Learning Engineer', 'Sr Machine Learning Engineering Manager', 'Lead Machine Learning Engineer - Localization', 'Werkstudent Sales', 'Law Student Volunteer', 'Monitoring, Evaluation and Learning Consultant', 'Humane Studies Fellowship for PhD Students', 'AI Tutor - Writing', 'Research Lead, Pre-Training Safety', 'Research Engineer, Pre-training', 'Field Technician (Paid Training)', 'Academic Expert (SME) - Procurement KPI Framework', 'Organizer In-Training', 'Member of Technical Staff, Training Infra Engineer']:
            self.job(title, salary_min=200000, salary_max=300000)
        self.job('Teacher', is_active=False)
        response = self.client.get(self.education)
        self.assertEqual({job.pk for job in response.context['jobs']}, {job.pk for job in included})
        self.assertEqual(response.context['job_count'], len(included))
        self.assertNotContains(response, '>Machine Learning Engineer<')
        self.assertIsNone(response.context.get('salary_stats'))
        self.assertContains(response, '<title>Remote Education Jobs | Teaching &amp; Learning Design</title>', html=True)
        self.assertNotContains(response, 'from anywhere')

        directory = self.client.get(reverse('jobs:all_role_pages'))
        education = next(page for page in directory.context['role_pages'] if page['slug'] == 'remote-education-jobs')
        self.assertEqual(education['job_count'], len(included))

    def test_targeted_filters_preserve_membership_counts_and_canonical(self):
        match = self.job('Curriculum Designer', country='Canada')
        self.job('Teacher', country='Canada')
        self.job('Curriculum Designer', country='USA')
        self.job('Machine Learning Engineer', country='Canada')
        response = self.client.get(self.education, {'q': 'Curriculum', 'country': 'Canada'})
        self.assertEqual([job.pk for job in response.context['jobs']], [match.pk])
        self.assertEqual(response.context['job_count'], 1)
        self.assertContains(response, 'name="robots" content="noindex, follow"')
        self.assertContains(response, f'https://remoteimpact.org{self.education}')
        self.assertContains(response, 'value="Canada" selected')
        self.assertContains(response, 'not verified applicant eligibility')
        self.assertIn(('Teaching', 1), [(role['label'], role['count']) for role in response.context['destination_roles']])
        self.assertNotContains(response, 'jobs by impact area')

    def test_filter_state_survives_pagination_and_unknown_location(self):
        Job.objects.bulk_create([Job(title='Curriculum Designer', slug=f'page-{n}', organization=self.org, source='manual', country='Canada') for n in range(26)])
        response = self.client.get(self.education, {'q': 'Curriculum', 'country': 'Canada'})
        self.assertContains(response, '?page=2&amp;q=Curriculum&amp;country=Canada')
        next_page = self.client.get(self.education, {'q': 'Curriculum', 'country': 'Canada', 'page': '2'})
        self.assertEqual(len(next_page.context['jobs']), 1)
        empty = self.client.get(self.education, {'country': 'Unknown location'})
        self.assertEqual(empty.context['job_count'], 0)
        self.assertContains(empty, 'value="Unknown location" selected')
        self.assertContains(empty, 'Clear filters')
        self.assertNotContains(empty, 'Teaching (0)')

    def test_sustainability_explicit_specialties_and_filters(self):
        expected = self.job('ESG Reporting Analyst', country='Canada')
        self.job('Clean Energy Program Manager', country='USA')
        self.job('Decarbonization Researcher', country='UK')
        self.job('Software Engineer', country='Canada')
        response = self.client.get(self.sustainability, {'q': 'ESG', 'country': 'Canada'})
        self.assertEqual([job.pk for job in response.context['jobs']], [expected.pk])
        self.assertIn(('ESG', 1), [(role['label'], role['count']) for role in response.context['destination_roles']])
        self.assertContains(response, 'seo_sustainability')

    def test_each_destination_has_editable_scout_handoff_and_no_subscription_side_effect(self):
        self.job('Teacher')
        for url, label in [(self.education, 'education'), (self.sustainability, 'sustainability'), (reverse('jobs:keyword_jobs', args=['remote-nonprofit-jobs']), 'nonprofit')]:
            response = self.client.get(url)
            self.assertContains(response, f'Personalize this {label} search')
            self.assertContains(response, f'source=seo_{label}')
            self.assertContains(response, 'A preview does not subscribe you to emails')
            self.assertContains(response, 'search-destination-analytics.js')
        self.assertFalse(E.objects.exists())

    def test_salary_samples_explain_missing_maximum_and_exclude_nonannual_or_foreign_pay(self):
        self.job('Teacher', salary_min=100000, salary_max=200000, salary_currency='USD', raw_data={'salary_period': 'year'})
        self.job('Curriculum Lead', salary_min=50000, salary_currency='USD', raw_data={'salary_period': 'year'})
        self.job('Teacher', salary_min=200000, salary_max=300000, salary_currency='EUR', raw_data={'salary_period': 'year'})
        self.job('Teacher', salary_min=200000, salary_max=300000, salary_currency='USD', raw_data={'salary_period': 'hour'})
        response = self.client.get(self.education)
        self.assertEqual(response.context['salary_stats']['count'], 2)
        self.assertEqual(response.context['salary_stats']['max_count'], 1)
        self.assertContains(response, '$75,000')
        self.assertContains(response, 'average stated minimum')
        self.assertContains(response, 'not a market salary range')
        self.assertContains(response, 'Listings in this search')

    def test_item_list_count_matches_actual_filtered_inventory(self):
        self.job('Teacher')
        self.job('Machine Learning Engineer')
        response = self.client.get(self.education)
        blocks = [json.loads(block) for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', response.content.decode(), re.S)]
        listing = next(block for block in blocks if block.get('@type') == 'ItemList')
        self.assertEqual(listing['numberOfItems'], 1)
        self.assertEqual(len(listing['itemListElement']), 1)

    def test_cohort_report_does_not_count_old_users_or_trial_events_as_revenue(self):
        user = get_user_model().objects.create_user('new-cohort', 'cohort@example.org', 'synthetic-test-password')
        other = get_user_model().objects.create_user('unlinked', 'other@example.org', 'synthetic-test-password')
        metadata = {'scout_flow_id': 'a' * 32, 'scout_source': 'seo_education'}
        E.objects.create(event=E.Event.SCOUT_PREVIEWED, metadata=metadata)
        E.objects.create(event=E.Event.ACCOUNT_CREATED, user=user, metadata=metadata)
        E.objects.create(event=E.Event.SCOUT_CREATED, user=user, metadata=metadata)
        E.objects.create(event=E.Event.PAYMENT_COMPLETED, user=user, metadata={'trial': True})
        E.objects.create(event=E.Event.PAYMENT_COMPLETED, user=other, metadata={})
        output = StringIO()
        call_command('search_destination_report', stdout=output)
        report = json.loads(output.getvalue())
        row = report['by_destination']['education']
        self.assertEqual(row['linked_new_flows'], 1)
        self.assertEqual(row['stage_flows']['account_created'], 1)
        self.assertEqual(row['recorded_trial_start_events'], 1)
        self.assertEqual(row['other_completion_events'], 0)
        self.assertNotIn('cohort@example.org', output.getvalue())
        self.assertNotIn('revenue', row)
