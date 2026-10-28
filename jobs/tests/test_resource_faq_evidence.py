import json
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class ClimateGuideFAQTests(SimpleTestCase):
    def test_structured_answers_are_visible_and_do_not_invent_employer_rankings(self):
        provider = Mock(get_login_url=Mock(return_value='/accounts/google/login/'))
        with patch('allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_provider', return_value=provider):
            html = render_to_string('resources/pillars/climate-environment.html')
        soup = BeautifulSoup(html, 'html.parser')
        schemas = [json.loads(script.string) for script in soup.find_all('script', type='application/ld+json')]
        faq = next(schema for schema in schemas if schema.get('@type') == 'FAQPage')
        self.assertEqual(len(faq['mainEntity']), 4)
        for question in faq['mainEntity']:
            with self.subTest(question=question['name']):
                heading = soup.find('h3', string=question['name'])
                self.assertIsNotNone(heading)
                self.assertEqual(heading.find_next_sibling('p').get_text(' ', strip=True), question['acceptedAnswer']['text'])
        answers = ' '.join(item['acceptedAnswer']['text'] for item in faq['mainEntity'])
        self.assertNotIn('Most entry-level climate roles require', answers)
        self.assertNotIn('are growing fast', answers)
        self.assertIn('does not rank employers by hiring volume', answers)
