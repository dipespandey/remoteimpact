from bs4 import BeautifulSoup
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


class UnavailableJobRecoveryTests(SimpleTestCase):
    def render_notice(self, related_jobs):
        return BeautifulSoup(render_to_string('jobs/partials/unavailable_job_notice.html',
            {'related_jobs': related_jobs}), 'html.parser')

    def test_unavailable_notice_links_to_existing_current_opportunities_section(self):
        soup = self.render_notice([object()])
        self.assertIn('no longer available', soup.get_text())
        self.assertIsNotNone(soup.find('a', href='#similar-roles-heading'))
        self.assertIsNotNone(soup.find('a', href='/jobs/'))
        self.assertNotIn('Apply', soup.get_text())

    def test_no_current_related_jobs_has_no_dead_anchor(self):
        soup = self.render_notice([])
        self.assertIsNone(soup.find('a', href='#similar-roles-heading'))
        self.assertEqual(soup.find('a')['href'], '/jobs/')
        self.assertIn('Browse current jobs', soup.get_text())


class UnavailableJobPublicationTests(TestCase):
    def test_recovery_keeps_historical_page_noindex_without_an_application_action(self):
        import json
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        from jobs.models import Category, Job, Organization
        for provider, provider_id in [('google', ''), ('openid_connect', 'linkedin')]:
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider,
                client_id='test-client', secret='test-secret',
                settings={'server_url': 'https://www.linkedin.com/oauth'} if provider_id else {})
            app.sites.add(Site.objects.get_current())
        org = Organization.objects.create(name='Recovery Example', slug='recovery-example')
        category = Category.objects.create(name='Climate', slug='recovery-climate')
        fields = dict(organization=org, category=category, source='idealist', description='Source role', quality_score=80)
        closed = Job.objects.create(title='Historical role', slug='historical-recovery', is_active=False,
            application_url='https://example.com/historical-application', **fields)
        current = Job.objects.create(title='Current role', slug='current-recovery', **fields)
        response = self.client.get(reverse('jobs:job_detail', args=[closed.slug]))
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertEqual(soup.find('meta', attrs={'name': 'robots'})['content'], 'noindex, follow')
        schemas = [json.loads(script.string) for script in soup.find_all('script', type='application/ld+json')]
        self.assertFalse(any(schema.get('@type') == 'JobPosting' for schema in schemas))
        self.assertIsNotNone(soup.find('a', href='#similar-roles-heading'))
        self.assertIsNotNone(soup.find(id='similar-roles-heading'))
        self.assertIsNotNone(soup.find('a', href=current.get_absolute_url()))
        self.assertIsNone(soup.find('a', href=closed.application_url))
