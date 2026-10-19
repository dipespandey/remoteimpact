from datetime import date
import json
import re
from unittest.mock import patch

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from jobs.services.early_careers import FILTERS, opportunities, registry
from jobs.sitemaps import StaticSitemap


class ProgrammeIntegrityTests(SimpleTestCase):
    def test_reviewed_registry_is_unique_and_source_backed(self):
        rows = registry()
        self.assertGreaterEqual(len(rows), 40)
        self.assertEqual(len({r['slug'] for r in rows}), len(rows))
        for row in rows:
            self.assertTrue(row['source_url'].startswith('https://'))
            date.fromisoformat(row['checked_on'])
            for key, choices in FILTERS.items():
                self.assertIn(row[key], choices)
            for key in ['requirements', 'funding_detail', 'location', 'duration', 'fit', 'timing']:
                self.assertTrue(row[key])

    def test_open_claim_expires_without_an_editor_recheck(self):
        fresh = opportunities(date(2026, 10, 19))
        stale = opportunities(date(2026, 11, 3))
        self.assertTrue(any(r['status'] == 'open' for r in fresh))
        self.assertFalse(any(r['status'] in {'open', 'listings'} for r in stale))
        self.assertTrue(all(r['review_due'] for r in stale))

    def test_deadline_closes_and_closing_soon_is_bounded(self):
        row = dict(registry()[0], deadline='2026-10-21')
        with patch('jobs.services.early_careers.registry', return_value=[row]):
            self.assertTrue(opportunities(date(2026, 10, 19))[0]['closing_soon'])
            self.assertEqual(opportunities(date(2026, 10, 22))[0]['status'], 'closed')
        self.assertFalse(any(r['closing_soon'] for r in opportunities(date(2026, 10, 19))))

    def test_paid_training_and_travel_are_not_misclassified(self):
        rows = {r['slug']: r for r in opportunities(date(2026, 10, 19))}
        self.assertEqual(rows['terra-learning-for-action']['funding'], 'fee')
        self.assertEqual(rows['climatebase']['funding'], 'fee')
        self.assertNotEqual(rows['claude-corps']['mode'], 'remote')
        self.assertEqual(rows['iaps']['mode'], 'hybrid')
        self.assertEqual(rows['outreachy']['mode'], 'remote')


class EarlyCareerPageTests(TestCase):
    def setUp(self):
        site = Site.objects.get_current()
        for provider, provider_id in [('google',''), ('openid_connect','linkedin')]:
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider, client_id='test', secret='test', settings={'server_url':'https://www.linkedin.com/oauth'} if provider_id else {})
            app.sites.add(site)
        self.clock = patch('jobs.services.early_careers.timezone.localdate', return_value=date(2026,10,19))
        self.clock.start(); self.addCleanup(self.clock.stop)

    def test_hub_is_public_canonical_and_not_jobposting_schema(self):
        response = self.client.get(reverse('jobs:early_careers'))
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertContains(response, 'Claude Corps')
        self.assertContains(response, 'UNICEF Internship Programme')
        self.assertContains(response, 'index, follow')
        data = next(item for item in [json.loads(raw) for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.S)] if item.get('@type') == 'CollectionPage')
        self.assertEqual(data['@type'], 'CollectionPage')
        self.assertNotIn('JobPosting', text)
        self.assertEqual(data['mainEntity']['numberOfItems'], len(registry()))
        self.assertIn('jobs:early_careers', [row[0] for row in StaticSitemap().items()])
        self.assertContains(response, '/jobs/?experience=entry')
        self.assertContains(response, '/remote-intern-jobs/')

    def test_filters_intersect_and_paid_does_not_include_fee_courses(self):
        response = self.client.get(reverse('jobs:early_careers'), {'funding':'paid','audience':'first-step'})
        self.assertGreaterEqual(response.context['result_count'], 4)
        self.assertTrue(all(r['funding'] == 'paid' and r['audience'] == 'first-step' for group in response.context['groups'] for r in group['items']))
        self.assertContains(response, 'Claude Corps')
        self.assertNotContains(response, 'Climate Change: Learning for Action')
        self.assertContains(response, 'noindex, follow')
        self.assertContains(response, 'value="paid" selected')

    def test_empty_filter_retains_controls_and_offers_reset(self):
        response = self.client.get(reverse('jobs:early_careers'), {'funding':'fee','kind':'internship'})
        self.assertEqual(response.context['result_count'], 0)
        self.assertContains(response, 'No programmes match these filters yet')
        self.assertContains(response, 'View all programmes')

    def test_unknown_filters_do_not_hide_programmes_or_reflect_markup(self):
        response = self.client.get(reverse('jobs:early_careers'), {'kind':'<script>alert(1)</script>'})
        self.assertEqual(response.context['result_count'], len(registry()))
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_search_finds_organisations_and_intersects_with_filters(self):
        response = self.client.get(reverse('jobs:early_careers'), {'q': 'UNHCR', 'funding': 'paid'})
        self.assertEqual(response.context['result_count'], 1)
        self.assertContains(response, 'UNHCR Internships')
        self.assertContains(response, 'noindex, follow')
        response = self.client.get(reverse('jobs:early_careers'), {'q': 'UNHCR', 'funding': 'fee'})
        self.assertEqual(response.context['result_count'], 0)

    def test_unpaid_and_upcoming_rounds_are_explicit(self):
        response = self.client.get(reverse('jobs:early_careers'), {'funding': 'unpaid'})
        slugs = {r['slug'] for g in response.context['groups'] for r in g['items']}
        self.assertIn('animal-outlook', slugs)
        self.assertIn('spar', slugs)
        self.assertNotIn('outreachy', slugs)
        rows = {r['slug']: r for r in opportunities(date(2026, 10, 19))}
        self.assertEqual(rows['wwf-bridge']['status'], 'check')
        self.assertEqual(rows['ecdc-traineeships']['status'], 'closed')
        self.assertEqual(rows['pibbss']['status'], 'closed')

    def test_search_escapes_markup_and_resources_do_not_inflate_count(self):
        response = self.client.get(reverse('jobs:early_careers'), {'q': '<script>alert(1)</script>'})
        self.assertEqual(response.context['result_count'], 0)
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, 'https://jobs.80000hours.org/')
        self.assertContains(response, 'https://probablygood.org/resources/internships/')
        self.assertEqual(response.context['total'], len(registry()))
