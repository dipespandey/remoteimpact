from django.test import TestCase
from django.urls import reverse

from jobs.seo_config import KEYWORD_SEO_PAGES, get_keyword_page_config


class ClimateDestinationTests(TestCase):
    def test_duplicate_redirect_preserves_filters_pagination_and_campaign(self):
        old = reverse('jobs:keyword_jobs', args=['remote-climate-jobs'])
        target = reverse('jobs:category_landing', args=['climate-environment'])
        query = '?page=2&q=engineer&type=part-time&utm_source=partner'
        response = self.client.get(old + query)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], target + query)
        response = self.client.get(old)
        self.assertEqual(response['Location'], target)

    def test_duplicate_is_absent_from_keyword_discovery_and_sitemap_configuration(self):
        self.assertNotIn('remote-climate-jobs', [p['slug'] for p in KEYWORD_SEO_PAGES])
        self.assertIsNone(get_keyword_page_config('remote-climate-jobs'))
