from datetime import date
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

from django.test import TestCase, override_settings
from django.urls import reverse

from earlycareers.feeds import EarlyCareersFeed
from jobs.services.early_careers import opportunities, registry


@override_settings(SITE_URL='https://remoteimpact.org')
class ProgrammeFeedTests(TestCase):
    def render_feed(self, rows):
        with patch('earlycareers.feeds.opportunities', return_value=rows):
            response = self.client.get(reverse('jobs:early_careers_feed'))
        self.assertEqual(response.status_code, 200)
        return ET.fromstring(response.content)

    def test_public_feed_omits_closed_and_retains_upcoming_and_locations(self):
        rows = opportunities(date(2026, 10, 19))
        root = self.render_feed(rows)
        items = root.findall('./channel/item')
        self.assertEqual(len(items), sum(r['status'] != 'closed' for r in rows))
        by_guid = {i.findtext('guid').rsplit('#', 1)[-1]: i for i in items}
        self.assertNotIn('pibbss', by_guid)
        self.assertIn('wwf-bridge', by_guid)
        claude = by_guid['claude-corps']
        self.assertIn('US authorization must not require employer sponsorship', claude.findtext('description'))
        self.assertIn('Varies by placement', claude.findtext('description'))
        self.assertNotIn('utm_', claude.findtext('guid'))
        self.assertEqual(urlsplit(claude.findtext('link')).fragment, 'claude-corps')
        self.assertEqual(parse_qs(urlsplit(claude.findtext('link')).query)['utm_campaign'], ['early_careers_feed'])

    def test_expired_deadline_leaves_feed_and_overdue_review_loses_open_claim(self):
        raw = dict(registry()[0], deadline='2026-10-20')
        with patch('jobs.services.early_careers.registry', return_value=[raw]):
            self.assertEqual(len(self.render_feed(opportunities(date(2026, 10, 21))).findall('./channel/item')), 0)
        rows = opportunities(date(2026, 11, 3))
        root = self.render_feed(rows)
        claude = next(i for i in root.findall('./channel/item') if i.findtext('guid').endswith('#claude-corps'))
        description = claude.findtext('description')
        self.assertIn('Review overdue', description)
        self.assertIn('Check next intake', description)
        self.assertNotIn('Applications open', description)

    def test_untrusted_source_text_is_escaped_and_guid_is_host_independent(self):
        row = opportunities(date(2026, 10, 19))[0]
        row.update(fit='<script>alert(1)</script>', requirements='R&D <img src=x onerror=alert(1)>')
        root = self.render_feed([row])
        description = root.findtext('./channel/item/description')
        self.assertNotIn('<script>', description)
        self.assertNotIn('<img ', description)
        self.assertIn('&lt;script&gt;', description)
        self.assertIn('R&amp;D', description)
        self.assertTrue(root.findtext('./channel/item/guid').startswith('https://remoteimpact.org/'))
        self.assertEqual(EarlyCareersFeed().item_updateddate(row).date(), row['checked_date'])
