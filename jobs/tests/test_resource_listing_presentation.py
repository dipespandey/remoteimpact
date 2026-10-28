import json
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from django.test import TestCase
from django.db.models import F

from jobs.models import Job, Organization


class ResourceListingPresentationTests(TestCase):
    def setUp(self):
        provider = patch('allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_provider',
            return_value=Mock(get_login_url=Mock(return_value='/test-login/')))
        provider.start()
        self.addCleanup(provider.stop)
        self.org = Organization.objects.create(name='Resource Example', slug='resource-example')

    def job(self, **overrides):
        fields = dict(title='Impact Navigator', slug='impact-navigator', organization=self.org,
            source=Job.Source.PROBABLYGOOD, is_active=True, quality_score=80,
            description='Free career advice and practical support for your next steps. ' * 4,
            raw_data={'tags_workload': ['Training']},
            application_url='https://example.org/advising/')
        fields.update(overrides)
        return Job.objects.create(**fields)

    def test_resource_page_never_claims_employment_even_before_cleanup(self):
        job = self.job()
        response = self.client.get(job.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertEqual(soup.find('meta', attrs={'name': 'robots'})['content'], 'noindex, follow')
        self.assertIn('resource, not a job opening', soup.get_text())
        self.assertIsNone(soup.select_one('header dl').find('dt', string='Type'))
        self.assertIsNone(soup.find('a', href=job.get_absolute_url() + 'go/'))
        resource = soup.find('a', href=lambda href: href and href.startswith(job.application_url))
        self.assertEqual(resource.get_text(strip=True), 'View original resource')
        self.assertIn('nofollow', resource['rel'])
        schemas = [json.loads(s.string) for s in soup.find_all('script', type='application/ld+json')]
        self.assertFalse(any(s.get('@type') == 'JobPosting' for s in schemas))
        self.assertNotIn('employer has closed', soup.get_text())

    def test_resource_access_does_not_render_an_unsafe_destination(self):
        response = self.client.get(self.job(application_url='javascript:alert(1)').get_absolute_url())
        self.assertNotContains(response, 'View original resource')
        self.assertNotContains(response, 'href="javascript:')

    def test_old_guide_and_apply_links_recover_without_counting_an_application(self):
        job = self.job()
        response = self.client.get(job.get_absolute_url() + 'application-guide/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], job.get_absolute_url())
        with patch('jobs.views.tracking.job_stats.record') as record:
            response = self.client.get(job.get_absolute_url() + 'go/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], job.get_absolute_url())
        record.assert_not_called()

    def test_actual_training_staff_role_keeps_its_employment_details(self):
        job = self.job(title='Training Coordinator', raw_data={'tags_workload': ['Full-Time']},
            description='Full-time remote job coordinating staff training. ' * 5)
        response = self.client.get(job.get_absolute_url())
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertEqual(soup.find('meta', attrs={'name': 'robots'})['content'], 'index, follow')
        self.assertIsNotNone(soup.select_one('header dl').find('dt', string='Type'))
        self.assertIn('"@type": "JobPosting"', response.content.decode())

    def test_scout_source_gate_does_not_fetch_deferred_source_json(self):
        from jobs.services.scout_constraints import evaluate_job
        from jobs.services.scout_workflow import bounded_workflow
        job = self.job()
        projected = (Job.objects.annotate(scout_source_workloads=F('raw_data__tags_workload'))
            .select_related('organization').defer('raw_data').get(pk=job.pk))
        with self.assertNumQueries(0), bounded_workflow({}):
            self.assertEqual(evaluate_job(projected, {}, True).reason, 'not_job_opening')
        self.assertIn('raw_data', projected.get_deferred_fields())
