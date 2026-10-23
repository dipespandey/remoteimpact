import copy
import json
import os
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job, Organization, RoundupSubscriber
from jobs.services.early_careers import opportunities, registry as programme_registry
from jobs.services.roundup_issue import issue, registry, validate_issues
from jobs.sitemaps import RoundupIssueSitemap


def social_apps():
    for provider, provider_id in (('google', ''), ('openid_connect', 'linkedin')):
        app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider,
            client_id='test', secret='test', settings={'server_url': 'https://www.linkedin.com/oauth'} if provider_id else {})
        app.sites.add(Site.objects.get_current())


def create_reviewed_jobs():
    for pick in registry()[0]['picks'].values():
        org, _ = Organization.objects.get_or_create(pk=pick['organization_id'], defaults={
            'name': pick['organization'], 'slug': 'reviewed-' + str(pick['organization_id'])})
        Job.objects.create(pk=pick['job_id'], slug=pick['job_slug'], organization=org,
            title=pick['title'], description='Reviewed fixture.', requirements='See source.',
            application_url=pick['expected_catalogue_application_url'], is_active=True,
            posted_at=timezone.now(), quality_score=75)


class IssueRegistryTests(SimpleTestCase):
    def test_eight_distinct_picks_shared_by_three_sections(self):
        rows = validate_issues(registry(), date(2026, 10, 22))
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['picks']), 8)
        self.assertEqual([s['slug'] for s in rows[0]['sections']], ['climate', 'research-software', 'early-career'])
        self.assertGreater(sum(len(s['pick_keys']) for s in rows[0]['sections']), 8)

    def test_invalid_dates_identities_urls_and_references_fail(self):
        mutations = [
            lambda row: row.update(published_on='2026-10-23'),
            lambda row: row['picks'][next(iter(row['picks']))].update(checked_on='2026-10-23'),
            lambda row: row['picks'][next(iter(row['picks']))].update(checked_on='20261022'),
            lambda row: row['picks'][next(iter(row['picks']))].update(job_id=True),
            lambda row: row['picks'][next(iter(row['picks']))].update(source_url='javascript:alert(1)'),
            lambda row: row['sections'][0].update(pick_keys=['missing']),
            lambda row: row['sections'][0].update(pick_keys=[next(iter(row['picks']))] * 2),
            lambda row: row['sections'][0].update(search_path='//evil.example/'),
        ]
        for mutate in mutations:
            rows = copy.deepcopy(registry())
            mutate(rows[0])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                validate_issues(rows, date(2026, 10, 22))

    def test_only_six_programmes_rechecked_and_reviewed_corrections(self):
        rows = programme_registry()
        self.assertEqual(len(rows), 53)
        self.assertEqual(sum(r['checked_on'] == '2026-10-22' for r in rows), 6)
        self.assertEqual(sum(r['checked_on'] == '2026-10-19' for r in rows), 47)
        by_slug = {r['slug']: r for r in rows}
        self.assertEqual(by_slug['outreachy']['status'], 'closed')
        self.assertEqual(by_slug['outreachy']['deadline'], '2026-08-31')
        self.assertIn('Mandatory in-person Basecamp', by_slug['claude-corps']['requirements'])
        self.assertIn('without a year', by_slug['bluedot-governance']['timing'])
        self.assertNotEqual(by_slug['bluedot-governance']['funding'], 'paid')


@override_settings(SECURE_SSL_REDIRECT=False, SITE_URL='https://remoteimpact.org')
class IssuePageTests(TestCase):
    def setUp(self):
        social_apps()
        create_reviewed_jobs()
        self.clock = patch('jobs.services.roundup_issue.timezone.localdate', return_value=date(2026, 10, 22))
        self.clock_mock = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.url = reverse('jobs:roundup_issue', args=['2026-10-22'])

    def test_public_canonical_sections_and_no_external_or_sending_side_effects(self):
        count = RoundupSubscriber.objects.count()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No network on GET')), \
             patch('jobs.services.email_service.EmailService.send_email', side_effect=AssertionError('No mail on GET')), \
             patch('jobs.services.ai.AIClient.generate', side_effect=AssertionError('No model on GET')):
            response = self.client.get(self.url + '?job_id=999999')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="https://remoteimpact.org' + self.url + '"')
        for slug in ('climate', 'research-software', 'early-career'):
            self.assertContains(response, 'id="' + slug + '"')
        self.assertContains(response, 'No account needed')
        self.assertContains(response, 'Hiring-country eligibility needs confirmation')
        self.assertContains(response, 'No numerical salary published')
        self.assertEqual(RoundupSubscriber.objects.count(), count)
        self.assertEqual(response.context['issue']['pick_count'], 8)
        self.assertEqual({card['job_id'] for section in response.context['issue']['sections'] for card in section['cards']}, {pick['job_id'] for pick in registry()[0]['picks'].values()})
        self.assertNotContains(response, 'JobPosting')
        self.assertContains(response, '/newsletter/?source=roundup_issue')
        self.assertContains(response, 'data-track="roundup_issue_job_open"', count=13)

    def test_unknown_issue_returns_404_and_post_is_disallowed(self):
        self.assertEqual(self.client.get(reverse('jobs:roundup_issue', args=['unknown'])).status_code, 404)
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_source_and_catalogue_urls_can_differ_without_losing_verified_identity(self):
        picks = issue('2026-10-22')['sections'][2]['cards']
        cdi = next(p for p in picks if p['organization'] == 'Constructive Dialogue Institute')
        self.assertNotEqual(cdi['source_url'], cdi['expected_catalogue_application_url'])
        self.assertTrue(cdi['available'])

    def test_international_staff_role_keeps_employer_geography_and_source_identity(self):
        selection = issue('2026-10-22')
        role = next(card for section in selection['sections'] for card in section['cards']
                    if card['organization'] == 'Overstory')
        self.assertTrue(role['available'])
        self.assertIn('United Kingdom', role['location_summary'])
        self.assertIn('Switzerland', role['location_summary'])
        self.assertIn('10+ years', role['experience_summary'])
        self.assertNotEqual(role['source_url'], role['expected_catalogue_application_url'])
        self.assertIn('/application?', role['expected_catalogue_application_url'])
        Job.objects.filter(pk=role['job_id']).update(application_url='https://example.org/replacement')
        updated = issue('2026-10-22')
        self.assertFalse(any(card['available'] for section in updated['sections'] for card in section['cards']
                             if card['organization'] == 'Overstory'))

    def test_missing_closed_changed_identity_and_source_never_borrow_a_role(self):
        selected = registry()[0]['picks']
        key = next(iter(selected))
        reviewed = selected[key]
        job = Job.objects.get(pk=reviewed['job_id'])
        alternate = Organization.objects.create(name='Other employer', slug='other-employer')
        for change in ({'is_active': False}, {'organization_id': alternate.pk},
                       {'slug': 'substituted-job'}, {'application_url': 'https://example.org/changed'},
                       {'title': 'Changed title'}, {'expires_at': timezone.now() - timedelta(days=1)},
                       {'quality_score': 10}):
            with self.subTest(change=change):
                original = {field: getattr(job, field) for field in change}
                Job.objects.filter(pk=job.pk).update(**change)
                card = issue('2026-10-22')['sections'][0]['cards'][0]
                self.assertFalse(card['available'])
                self.assertEqual(card['job_url'], '')
                response = self.client.get(self.url)
                self.assertContains(response, 'Historical snapshot.')
                self.assertNotContains(response, reviewed['job_slug'] + '/?utm_source')
                Job.objects.filter(pk=job.pk).update(**original)
        job.delete()
        card = issue('2026-10-22')['sections'][0]['cards'][0]
        self.assertFalse(card['available'])
        self.assertEqual(card['job_url'], '')

    def test_dates_stay_fixed_and_old_source_checks_are_visible(self):
        fresh = issue('2026-10-22', date(2026, 10, 29))
        self.assertFalse(fresh['review_due'])
        stale = issue('2026-10-22', date(2026, 10, 30))
        self.assertTrue(stale['review_due'])
        self.assertEqual(stale['published_on'], '2026-10-22')
        self.clock_mock.return_value = date(2026, 10, 30)
        response = self.client.get(self.url)
        self.assertContains(response, 'Historical selection. Source checks are due for review.')
        self.assertContains(response, 'Employer source checked 22 October 2026')

    def test_landing_partner_and_sitemap_discover_issue(self):
        self.assertContains(self.client.get(reverse('jobs:roundup')), self.url)
        partner = self.client.get(reverse('jobs:partner_resources'))
        for slug in ('climate', 'research-software', 'early-career'):
            self.assertContains(partner, self.url + '#' + slug)
        sitemap = RoundupIssueSitemap()
        self.assertEqual(sitemap.location(sitemap.items()[0]), self.url)
        self.assertContains(self.client.get('/sitemap-roundup-issues.xml'), self.url)

    def test_access_report_dates_and_intersection_do_not_overstate_available_jobs(self):
        with patch('jobs.services.early_careers.timezone.localdate', return_value=date(2026, 10, 22)):
            response = self.client.get(reverse('jobs:early_careers_access_report'))
        self.assertEqual(response.context['latest_reviewed_count'], 6)
        self.assertEqual(response.context['oldest_reviewed'], date(2026, 10, 19))
        self.assertEqual(response.context['last_reviewed'], date(2026, 10, 22))
        self.assertEqual(response.context['review_due_count'], 0)
        rows = opportunities(date(2026, 10, 22))
        intersection = sum(r['kind'] == 'internship' and r['funding'] == 'paid'
            and r['mode'] == 'remote' and r['status'] == 'open' for r in rows)
        self.assertEqual(response.context['open_paid_remote_internships'], intersection)
        self.assertContains(response, 'not a count of paid jobs currently available')
        self.assertNotContains(response, 'Directory reviewed through')
        self.assertContains(response, '6 of 53 entries were checked on Oct 22, 2026')

    def test_access_report_zero_intersection_and_overdue_counts_remain_honest(self):
        rows = opportunities(date(2026, 11, 20))
        with patch('jobs.views.early_careers.opportunities', return_value=rows):
            response = self.client.get(reverse('jobs:early_careers_access_report'))
        self.assertEqual(response.context['open_paid_remote_internships'], 0)
        self.assertEqual(response.context['review_due_count'], 53)
        self.assertContains(response, '0 open, paid, fully remote internship programmes')


@override_settings(SECURE_SSL_REDIRECT=False)
class IssueBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        social_apps()
        create_reviewed_jobs()

    @patch.dict(os.environ, {'DJANGO_ALLOW_ASYNC_UNSAFE': 'true'})
    def test_mobile_desktop_topics_and_signup_navigation(self):
        from playwright.sync_api import sync_playwright, expect

        folder = Path('.audit/free-acquisition-ship-2026-10-22')
        with sync_playwright() as p:
            executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
            chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            if not executable and Path(chrome).exists():
                executable = chrome
            browser = p.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
            context = browser.new_context(viewport={'width': 360, 'height': 800})
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.live_server_url) else route.abort())
            page = context.new_page()
            response = page.goto(self.live_server_url + reverse('jobs:roundup_issue', args=['2026-10-22']))
            self.assertEqual(response.status, 200)
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 360)
            page.get_by_role('navigation', name='Job pick topics').get_by_role('link', name='Early-career routes with clear requirements').click()
            self.assertTrue(page.url.endswith('#early-career'))
            expect(page.locator('#early-career')).to_be_visible()
            page.screenshot(path=str(folder / 'issue-mobile.png'), full_page=True)
            page.locator('header.page-header').scroll_into_view_if_needed()
            page.screenshot(path=str(folder / 'issue-mobile-viewport.png'))
            first_source = page.locator('[data-pick]').first.get_by_role('link', name='Check employer source')
            self.assertEqual(urlsplit(first_source.get_attribute('href')).path, urlsplit(registry()[0]['picks'][next(iter(registry()[0]['picks']))]['source_url']).path)
            page.set_viewport_size({'width': 1440, 'height': 900})
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 1440)
            page.screenshot(path=str(folder / 'issue-desktop.png'), full_page=True)
            page.locator('header.page-header').scroll_into_view_if_needed()
            page.screenshot(path=str(folder / 'issue-desktop-viewport.png'))
            page.get_by_role('link', name='Get the weekly picks', exact=True).click()
            expect(page.get_by_role('heading', name='Get the weekly picks', exact=True)).to_be_visible()
            self.assertEqual(page.locator('[name="source"]').input_value(), 'roundup_issue')
            self.assertEqual(page.locator('[name="campaign"]').input_value(), 'issue_2026-10-22')
            self.assertEqual(RoundupSubscriber.objects.count(), 0)
            browser.close()
