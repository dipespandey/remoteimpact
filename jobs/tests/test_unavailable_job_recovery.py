from bs4 import BeautifulSoup
from django.template.loader import render_to_string
from django.test import SimpleTestCase


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
