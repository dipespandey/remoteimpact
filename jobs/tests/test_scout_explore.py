from unittest.mock import patch
import os
from pathlib import Path
from urllib.parse import urlencode

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import TestCase, override_settings
from django.urls import reverse

from jobs.models import ConsumerFunnelEvent, JobAlert, JobAlertDelivery
from jobs.tests.test_scout_results import setup_scout_fixture


@override_settings(SECURE_SSL_REDIRECT=False, ACCOUNT_RATE_LIMITS={}, SCOUT_PREVIEW_ASYNC_ENABLED=False)
class ScoutExploreTests(TestCase):
    def setUp(self):
        setup_scout_fixture(self)

    def test_empty_preview_shows_options_without_claiming_verified_matches(self):
        with patch('jobs.services.job_scout_service.JobScoutService.search_intent', return_value=(self.prefs, [])):
            page = self.client.get(reverse('jobs:job_scout_preview'), {'intent': self.intent})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Possible roles to explore')
        self.assertContains(page, self.job.title)
        self.assertContains(page, 'not verified matches')
        self.assertNotContains(page, 'No eligible matches in this preview')
        self.assertEqual(page.context['preview_jobs'], [])
        self.assertEqual([j.pk for j in page.context['explore_jobs']], [self.job.pk])
        event = ConsumerFunnelEvent.objects.get(event='scout_previewed')
        self.assertEqual(event.metadata['match_count'], 0)
        self.assertEqual(event.metadata['explore_count'], 1)
        self.assertFalse(JobAlertDelivery.objects.exists())

    def test_incomplete_preview_shows_options_without_a_second_model_call(self):
        def search(*args, **kwargs):
            kwargs['diagnostics']['review_incomplete'] = True
            return self.prefs, []
        with patch('jobs.services.job_scout_service.JobScoutService.search_intent', side_effect=search) as search_call, \
             patch('jobs.services.scout_workflow.generate', side_effect=AssertionError('No additional provider calls')):
            page = self.client.get(reverse('jobs:job_scout_preview'), {'intent': self.intent})
        search_call.assert_called_once()
        self.assertTrue(page.context['review_incomplete'])
        self.assertContains(page, self.job.title)
        self.assertContains(page, 'Possible roles to explore')

    def test_saved_empty_result_offers_current_options_without_rewriting_ledger(self):
        from django.contrib.auth import get_user_model
        from jobs.services.scout_results import initial_result_context
        user = get_user_model().objects.create_user(username='explore-test')
        alert = JobAlert.objects.create(user=user, intent_text=self.intent, parsed_preferences=self.prefs)
        event = ConsumerFunnelEvent.objects.create(alert=alert, user=user,
            event=ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY,
            metadata={'results_version': 1, 'results': [], 'matching': {'review_incomplete': True}})
        with patch('jobs.services.job_scout_service.JobScoutService.get_matches', side_effect=AssertionError('No rematching')):
            context = initial_result_context(alert)
        self.assertEqual(context['preview_jobs'], [])
        self.assertTrue(context['initial_review_incomplete'])
        self.assertEqual([j.pk for j in context['explore_jobs']], [self.job.pk])
        event.refresh_from_db()
        self.assertEqual(event.metadata['results'], [])
        self.assertFalse(JobAlertDelivery.objects.exists())

    def test_known_conflict_is_visible_only_as_exploration_not_a_match(self):
        from jobs.services.scout_alternatives import explore_openings
        self.job.location = 'Remote Canada'
        self.job.country = 'CA'
        self.job.salary_min = 50000
        self.job.salary_max = 60000
        self.job.save(update_fields=['location', 'country', 'salary_min', 'salary_max'])
        prefs = {**self.prefs, 'salary_requirement': {'amount': '100000', 'currency': 'USD', 'period': 'year'}}
        result = explore_openings(prefs)
        self.assertEqual([j.pk for j in result], [self.job.pk])
        self.assertTrue(any('location' in note.lower() for note in result[0].explore_notes))
        self.assertTrue(any('pay' in note.lower() for note in result[0].explore_notes))
        self.assertTrue(any('Canada' in note for note in result[0].explore_notes))

    def test_courses_physical_closed_and_source_contradictions_are_never_options(self):
        from jobs.models import Job
        from jobs.services.scout_alternatives import explore_openings
        base = {'organization': self.job.organization, 'location': 'Remote worldwide'}
        examples = [
            dict(title='Course, AI Economics', application_url='https://www.coursera.org/learn/economics'),
            dict(title='Meals Driver', description='Deliver meals to consumers in their homes using your own vehicle.'),
            dict(title='Closed Engineer', is_active=False),
            dict(title='Deceptive Remote Engineer', raw_data={'description': 'Must work from our office every day.'}),
        ]
        for index, values in enumerate(examples):
            with patch('jobs.services.embedding_service.embed_job', return_value=None):
                Job.objects.create(slug=f'bad-explore-{index}', **{**base, **values})
        self.assertEqual([j.pk for j in explore_openings(self.prefs)], [self.job.pk])

    def test_successful_preview_does_not_run_exploration(self):
        with patch('jobs.services.job_scout_service.JobScoutService.search_intent', return_value=(self.prefs, [self.job])), \
             patch('jobs.services.scout_alternatives.explore_openings') as explore:
            self.client.get(reverse('jobs:job_scout_preview'), {'intent': self.intent})
        explore.assert_not_called()

    def test_no_inventory_is_honest_and_related_options_are_deduplicated(self):
        from jobs.services.scout_alternatives import explore_openings
        self.assertEqual(len(explore_openings(self.prefs)), 1)
        self.job.title = 'Finance Director'
        self.job.description = 'Financial Management (60%). Lead financial planning and remote team accounting.'
        self.job.save(update_fields=['title', 'description'])
        finance = {'intent_text': 'Remote finance leadership roles', 'titles': ['Finance Director']}
        self.assertEqual([j.pk for j in explore_openings(finance)], [self.job.pk])
        self.job.is_active = False
        self.job.save(update_fields=['is_active'])
        self.assertEqual(explore_openings(self.prefs), [])

    def test_related_roles_rank_first_and_duplicate_posts_do_not_fill_slots(self):
        from jobs.models import Job
        from jobs.services.scout_alternatives import explore_openings
        base = {'organization': self.job.organization, 'location': 'Remote worldwide'}
        with patch('jobs.services.embedding_service.embed_job', return_value=None):
            Job.objects.create(title=self.job.title, slug='duplicate-explore', description=self.job.description, **base)
            for index, title in enumerate(['Python Software Engineer', 'Backend Python Engineer', 'Software Engineer, Climate']):
                Job.objects.create(title=title, slug=f'explore-related-{index}', description='Build Python software and APIs.', **base)
            unrelated = Job.objects.create(title='Sales Director', slug='explore-unrelated', description='Lead sales campaigns and customer acquisition.', **base)
        result = explore_openings(self.prefs)
        self.assertEqual(len(result), 3)
        self.assertNotIn(unrelated.pk, [job.pk for job in result])
        self.assertEqual(len({job.title for job in result}), 3)

    @override_settings(SCOUT_PREVIEW_ASYNC_ENABLED=True)
    def test_failed_async_review_offers_options_but_pending_work_keeps_loading(self):
        from jobs.models import ScoutPreviewTask
        url = reverse('jobs:job_scout_preview')
        first = self.client.get(url, {'intent': self.intent})
        self.assertTrue(first.context['preview_pending'])
        self.assertEqual(first.context['explore_jobs'], [])
        task = ScoutPreviewTask.objects.get()
        task.state = ScoutPreviewTask.State.FAILED
        task.save(update_fields=['state'])
        with patch('jobs.services.scout_workflow.generate', side_effect=AssertionError('No new provider call')):
            page = self.client.get(url, {'intent': self.intent})
        self.assertFalse(page.context['preview_pending'])
        self.assertContains(page, 'Possible roles to explore')
        self.assertEqual(len(page.context['explore_jobs']), 1)


@override_settings(SECURE_SSL_REDIRECT=False, ACCOUNT_RATE_LIMITS={}, SCOUT_PREVIEW_ASYNC_ENABLED=False)
class ScoutExploreBrowserTests(StaticLiveServerTestCase):
    def test_probable_options_at_mobile_and_desktop_sizes(self):
        from playwright.sync_api import sync_playwright
        setup_scout_fixture(self)
        output = Path(os.environ.get('SCOUT_SCREENSHOT_DIR', '/tmp/scout-explore-browser'))
        output.mkdir(parents=True, exist_ok=True)
        with patch('jobs.services.job_scout_service.JobScoutService.search_intent', return_value=(self.prefs, [])), sync_playwright() as runtime:
            args = {'executable_path': os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE']} if os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') else {}
            browser = runtime.chromium.launch(headless=True, **args)
            page = browser.new_page()
            for width in [360, 1280]:
                page.set_viewport_size({'width': width, 'height': 900})
                page.goto(self.live_server_url + reverse('jobs:job_scout_preview') + '?' + urlencode({'intent': self.intent}))
                page.locator('#scout-explore-heading').wait_for(state='visible')
                self.assertIn('not verified matches', page.locator('main').inner_text())
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                link = page.locator('#scout-explore-heading + p + ul a').first
                link.focus()
                self.assertTrue(link.evaluate('(element) => document.activeElement === element'))
                page.screenshot(path=str(output / f'scout-explore-{width}.png'), full_page=True)
            browser.close()
