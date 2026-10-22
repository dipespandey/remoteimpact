from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from jobs.models import Job, Organization
from jobs.services.guide_generator import _create_job_hash, public_application_guide


class PublicApplicationGuideTests(TestCase):
    def setUp(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider, provider_id=provider_id, name=provider,
                client_id="test-client", secret="test-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(Site.objects.get_current())
        org = Organization.objects.create(name='Climate Lab', slug='climate-lab')
        self.job = Job.objects.create(title='Researcher', slug='researcher-guide',
                                      organization=org, description='Study climate.',
                                      source=Job.Source.MANUAL)

    @patch('jobs.services.guide_generator._generate_guide_content', side_effect=AssertionError('Public request generated AI'))
    def test_get_and_head_do_not_generate_or_save(self, generate):
        url = reverse('jobs:application_guide', args=[self.job.slug])
        for method in [self.client.get, self.client.head]:
            response = method(url)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context['guide_is_personalized'])
        response = self.client.get(url)
        self.assertContains(response, 'content="noindex, follow"')
        self.assertContains(response, 'General application guidance')
        self.job.refresh_from_db()
        self.assertFalse((self.job.raw_data or {}).get('application_guide'))
        generate.assert_not_called()

    def test_current_cached_guide_is_preserved(self):
        content = {'company_intro': 'Saved guide content'}
        self.job.raw_data = {'application_guide': {'hash': _create_job_hash(self.job), 'content': content}}
        self.job.save(update_fields=['raw_data'])
        response = self.client.get(reverse('jobs:application_guide', args=[self.job.slug]))
        self.assertContains(response, 'Saved guide content')
        self.assertContains(response, 'content="index, follow"')
        self.assertTrue(response.context['guide_is_personalized'])

    def test_stale_or_malformed_cache_uses_immediate_fallback(self):
        for cached in [None, 'invalid', {'hash': 'old', 'content': {'company_intro': 'Stale'}},
                       {'hash': _create_job_hash(self.job), 'content': 'invalid'}]:
            self.job.raw_data = {'application_guide': cached}
            guide, personalized = public_application_guide(self.job)
            self.assertFalse(personalized)
            self.assertIn('application_tips', guide)

    def test_missing_catalogue_deadline_does_not_claim_source_has_none(self):
        response = self.client.get(reverse('jobs:application_guide', args=[self.job.slug]))
        self.assertContains(response, 'We do not have a confirmed application deadline')
        self.assertNotContains(response, 'No deadline is listed')
        self.assertNotContains(response, 'so apply soon')

    def test_listing_expiration_is_not_presented_as_employer_application_deadline(self):
        from datetime import timedelta
        from django.utils import timezone
        self.job.expires_at = timezone.now() + timedelta(days=7)
        self.job.save(update_fields=['expires_at'])
        response = self.client.get(reverse('jobs:application_guide', args=[self.job.slug]))
        self.assertContains(response, 'This listing expires on')
        self.assertContains(response, "employer's application deadline")
        self.assertNotContains(response, 'Applications close on')

    def test_retired_source_guide_redirects_to_unavailable_detail(self):
        self.job.is_active = False
        self.job.save(update_fields=['is_active'])
        response = self.client.get(reverse('jobs:application_guide', args=[self.job.slug]))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], reverse('jobs:job_detail', args=[self.job.slug]))
