import json

from django.test import TestCase

from jobs.templatetags.job_extras import render_html


class DiscoverabilityMetadataTests(TestCase):
    def test_rendered_scraped_headings_do_not_create_extra_page_h1s(self):
        rendered = render_html("<h1>Employer section</h1><p>Details</p>")

        self.assertNotIn("<h1>", rendered.lower())
        self.assertIn("<h2>Employer section</h2>", rendered)

    def test_llms_txt_uses_public_routes_and_partner_assets(self):
        response = self.client.get('/llms.txt')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('/jobs/category/', body)
        self.assertIn('https://remoteimpact.org/feed/jobs/', body)
        self.assertIn('https://remoteimpact.org/resources/remote-impact-jobs/', body)
        self.assertIn('https://remoteimpact.org/insights/remote-impact-jobs/', body)
        self.assertIn('https://remoteimpact.org/pricing/', body)
        self.assertIn('Pro ongoing alerts at $10/month (7-day free trial)', body)
        self.assertNotIn('/jobs/feed/', body)
        self.assertNotIn('/salary-calculator/', body)

    def test_ai_manifest_exposes_feed_instead_of_dead_openapi_url(self):
        response = self.client.get('/.well-known/ai-plugin.json')

        self.assertEqual(response.status_code, 200)
        manifest = json.loads(response.content)
        self.assertEqual(manifest['feeds'][0]['url'], 'https://remoteimpact.org/feed/jobs/')
        self.assertNotIn('api', manifest)

    def test_humans_txt_names_current_hosting_provider(self):
        response = self.client.get('/humans.txt')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Hetzner VPS', response.content.decode())
