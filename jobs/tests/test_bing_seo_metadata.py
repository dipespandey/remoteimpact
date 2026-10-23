from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

from jobs.models import Job, Organization


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SOCIALACCOUNT_PROVIDERS={
        provider: {'APP': {'client_id': 'test', 'secret': 'test', 'key': ''}}
        for provider in ('google', 'linkedin')
    },
)
class BingSeoMetadataTests(TestCase):
    pages = {
        'remote-sustainability-jobs': (
            'role', 'Check eligible hiring locations and disclosed pay',
            'Climate Sustainability Analyst',
        ),
        'remote-conservation-jobs': (
            'keyword', 'Check hiring locations and pay details',
            'Remote Conservation Program Manager',
        ),
        'remote-foundation-jobs': (
            'keyword', 'Review funding focus, hiring locations and disclosed pay',
            'Remote Foundation Program Officer',
        ),
        'remote-fellow-jobs': (
            'role', 'Check eligibility, program format, duration and any published stipend',
            'Climate Policy Fellow',
        ),
        'remote-intern-jobs': (
            'role', 'Review eligibility, hiring locations and pay',
            'Global Health Intern',
        ),
    }

    def setUp(self):
        self.thread_patch = patch('jobs.signals.threading.Thread')
        self.embed_patch = patch('jobs.services.embedding_service.embed_job', return_value=[0.0] * 384)
        self.social_patch = patch(
            'allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_provider',
            return_value=Mock(get_login_url=Mock(return_value='/test-login/')),
        )
        for mocked in (self.thread_patch, self.embed_patch, self.social_patch):
            mocked.start()
            self.addCleanup(mocked.stop)

        self.organization = Organization.objects.create(
            name='Impact Test Organization', organization_type='nonprofit'
        )
        for slug, (_, _, title) in self.pages.items():
            for index in range(12):
                Job.objects.create(
                    title=title,
                    slug=f'{slug}-{index}',
                    organization=self.organization,
                    source='manual',
                )

    def test_bing_priority_pages_describe_live_count_and_decision_facts(self):
        for slug, (page_type, decision_facts, _) in self.pages.items():
            with self.subTest(page=slug):
                if page_type == 'role':
                    url = reverse('jobs:role_jobs', args=[slug])
                else:
                    url = reverse('jobs:keyword_jobs', args=[slug])

                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                description = response.context['meta_description']
                self.assertIn(f"{response.context['job_count']}+", description)
                self.assertIn(decision_facts, description)
                self.assertGreaterEqual(len(description), 140)
                self.assertLessEqual(len(description), 160)
                self.assertContains(
                    response,
                    f'<meta name="description" content="{description}">',
                    html=True,
                )
