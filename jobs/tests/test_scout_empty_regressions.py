"""Synthetic regressions for empty previews caused by over-constrained briefs."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from jobs.models import Category
from jobs.services.job_scout_service import JobScoutService as Scout
from jobs.services.scout_constraints import extract_constraints
from jobs.services.scout_evidence import role_conflict, experience_conflict


class ExclusionMeaningTests(SimpleTestCase):
    def test_nested_exclusion_does_not_exclude_the_seekers_country(self):
        prefs = extract_constraints('Remote jobs open to applicants from Pakistan. Exclude unpaid volunteering, US-only jobs, country-restricted roles that exclude Pakistan, and jobs requiring relocation.')
        self.assertEqual(prefs['locations'], ['Pakistan'])
        self.assertEqual(prefs['excluded_locations'], ['us'])
        self.assertIn('unpaid volunteering', prefs['exclude_keywords'])

    def test_do_not_exclude_is_not_an_exclusion(self):
        prefs = extract_constraints('Remote jobs from Pakistan. Do not exclude a job just because it does not mention Pakistan explicitly. Exclude internships.')
        self.assertEqual(prefs['locations'], ['Pakistan'])
        self.assertEqual(prefs['excluded_locations'], [])
        self.assertEqual(prefs['exclude_keywords'], ['internship'])

    def test_meta_exclusion_does_not_add_another_requested_country(self):
        prefs = extract_constraints("Remote jobs from Pakistan. Do not exclude jobs just because they mention the US.")
        self.assertEqual(prefs['locations'], ['Pakistan'])
        self.assertEqual(prefs['exclude_keywords'], [])

    def test_communications_recovery_does_not_expand_to_marketing(self):
        from jobs.services.scout_recovery import duty_evidence
        job = SimpleNamespace(title='Digital Marketing & Social Media Specialist',
            description='Develop marketing campaigns and social media strategy.', requirements='', raw_data={})
        self.assertIsNone(duty_evidence(job, {'titles':['Research Assistant','Communications Assistant']}))
        self.assertIsNotNone(duty_evidence(job, {'titles':['Marketing Specialist']}))

    def test_genuine_excluded_country_stays_excluded(self):
        prefs = extract_constraints('Remote jobs worldwide. Exclude Pakistan and US-only jobs.')
        self.assertEqual(set(prefs['excluded_locations']), {'pakistan', 'us'})

    def test_stated_three_plus_years_is_preserved(self):
        self.assertEqual(extract_constraints('I have 3+ years of teaching experience.')['candidate_years'], 3)

    def test_multiple_employment_arrangements_allow_consultants(self):
        job = SimpleNamespace(title='Research Consultant')
        self.assertFalse(role_conflict(job, {'job_types':['full-time','contract']}, 'Six-month contract.'))
        self.assertTrue(role_conflict(job, {'job_types':['full-time']}, 'Six-month contract.'))

    def test_explicit_candidate_years_can_meet_a_junior_floor(self):
        job = SimpleNamespace(title='Research Assistant')
        prefs = {'experience_levels':['entry'], 'candidate_years':3}
        self.assertFalse(experience_conflict(job, prefs, 'At least 2 years of experience required.'))
        self.assertTrue(experience_conflict(job, prefs, 'At least 5 years of experience required.'))
        self.assertTrue(experience_conflict(SimpleNamespace(title='Senior Researcher'), prefs, ''))


@override_settings(DEEPSEEK_API_KEY='')
class ExplicitRoleListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for slug, name in [('education','Education'),('communications','Communications'),('human-rights','Human Rights'),('nonprofit-charity','Nonprofit')]:
            Category.objects.create(slug=slug, name=name)

    brief = ('Find paid remote jobs open to applicants from Pakistan. Search for junior Research Assistant, '
             'Programme Assistant, Project Assistant, Communications Assistant, Education Support and Content Writing roles. '
             'Include entry-level jobs and short-term paid contracts. My qualifications are teaching experience.')

    def test_later_explicit_list_keeps_requested_functions(self):
        prefs = Scout._fallback_parse(self.brief)
        self.assertIn('junior research assistant', prefs['titles'])
        self.assertIn('programme assistant', prefs['titles'])
        self.assertIn('communications assistant', prefs['titles'])
        self.assertIn('education support', prefs['titles'])
        self.assertIn('content writing', prefs['titles'])
        self.assertEqual(prefs['job_types'], [])
        self.assertIn('contract', Scout._explicit_job_types('short-term paid contracts'))

    def test_role_function_is_not_a_compulsory_sector(self):
        prefs = Scout._fallback_parse(self.brief)
        self.assertNotIn('education', prefs.get('required_category_slugs', []))
        specific = Scout._fallback_parse('Search for Research Assistant or Communications Assistant roles in education.')
        self.assertIn('education', specific['required_category_slugs'])

    def test_sector_options_do_not_drop_the_nonprofit_option(self):
        prefs = Scout._fallback_parse('Find remote jobs in NGOs, education, human rights and communications. Search for Research Assistant or Programme Assistant roles.')
        self.assertEqual(prefs.get('category_match_mode'), 'any')

    def test_comma_before_employment_type_does_not_make_sector_optional(self):
        Category.objects.create(slug='climate-environment', name='Climate')
        Category.objects.create(slug='technology', name='Technology')
        prefs = Scout._fallback_parse('Search for software engineer roles in climate, full-time.')
        self.assertNotEqual(prefs.get('category_match_mode'), 'any')
        self.assertIn('climate-environment', prefs['required_category_slugs'])

    def test_sector_alternatives_do_not_include_the_function_category(self):
        Category.objects.create(slug='climate-environment', name='Climate')
        Category.objects.create(slug='technology', name='Technology')
        prefs = Scout._fallback_parse('Search for software engineer roles in climate or human rights.')
        self.assertEqual(prefs['category_match_mode'], 'any')
        self.assertEqual(set(prefs['required_category_options']), {'climate-environment','human-rights'})
        self.assertNotIn('technology', prefs['required_category_options'])

    def test_communications_is_one_alternative_not_a_compulsory_function(self):
        prefs = Scout._fallback_parse(self.brief)
        self.assertFalse(role_conflict(SimpleNamespace(title='Research Assistant'), prefs, 'Conduct research.'))
        self.assertFalse(role_conflict(SimpleNamespace(title='Programme Assistant'), prefs, 'Coordinate programmes.'))
        only = Scout._fallback_parse('Remote communications roles, full-time.')
        self.assertTrue(role_conflict(SimpleNamespace(title='Research Assistant'), only, 'Conduct research.'))

    def test_model_cannot_reintroduce_nested_or_geographic_keyword_exclusions(self):
        brief = 'Research Assistant jobs from Pakistan. Exclude unpaid volunteering, US-only jobs and country-restricted roles that exclude Pakistan.'
        fallback = Scout._fallback_parse(brief)
        prefs = Scout._ground_preferences({'exclude_keywords':['Pakistan','US','unpaid volunteering']}, brief, fallback)
        self.assertNotIn('pakistan', [v.lower() for v in prefs['exclude_keywords']])
        self.assertNotIn('us', [v.lower() for v in prefs['exclude_keywords']])
        self.assertIn('unpaid volunteering', prefs['exclude_keywords'])
        self.assertEqual(prefs['excluded_locations'], ['us'])

    def test_model_cannot_reintroduce_a_spurious_sector(self):
        fallback = Scout._fallback_parse(self.brief)
        prefs = Scout._ground_preferences({'titles':['Research Assistant','Communications Assistant'], 'category_slugs':['education']}, self.brief, fallback)
        self.assertEqual(prefs['required_category_slugs'], [])

    def test_background_does_not_create_requested_roles(self):
        prefs = Scout._fallback_parse('Looking for communications roles. My background includes teaching and research assistant experience.')
        self.assertNotIn('research assistant', prefs['titles'])

    def test_contract_drafting_is_still_a_skill(self):
        self.assertNotIn('contract', Scout._explicit_job_types('Legal work involving contract drafting and contract law'))

    def test_saved_scout_refreshes_old_broken_interpretation_without_writing(self):
        alert = SimpleNamespace(intent_text=self.brief, parsed_preferences={
            'titles':[], 'excluded_locations':['pakistan'], 'category_slugs':['education'],
            'category_match_mode':None,
        })
        prefs = Scout.preferences_for_alert(alert)
        self.assertNotIn('pakistan', prefs['excluded_locations'])
        self.assertIn('programme assistant', prefs['titles'])
        self.assertEqual(prefs['required_category_slugs'], [])
        self.assertEqual(alert.parsed_preferences['excluded_locations'], ['pakistan'])

    @override_settings(SCOUT_RERANK_ENABLED=False, SCOUT_MULTISTEP_ENABLED=False, SCOUT_CLOSE_MATCHES=False)
    def test_search_retrieves_alternative_functions_outside_education_and_rejects_conflicts(self):
        from jobs.models import Job, Organization
        from django.utils import timezone
        org = Organization.objects.create(name='Public Research Group', organization_type='nonprofit')
        for slug, title, location, level in [
            ('research-assistant', 'Research Assistant', 'Remote worldwide', 'entry'),
            ('communications-assistant', 'Communications Assistant', 'Remote worldwide', 'entry'),
            ('us-only-assistant', 'Research Assistant US', 'Remote United States only', 'entry'),
            ('senior-research', 'Senior Research Assistant', 'Remote worldwide', 'senior'),
        ]:
            Job.objects.create(slug=slug, title=title, organization=org,
                category=Category.objects.get(slug='human-rights'), location=location,
                country='Worldwide', experience_level=level, job_type='contract',
                description='Conduct research and write reports. Paid role. Remote work.',
                requirements='One year of experience.', posted_at=timezone.now())
        diagnostics = {}
        prefs, matches = Scout.search_intent(self.brief, diagnostics=diagnostics, limit=5)
        slugs = {job.slug for job in matches}
        self.assertIn('research-assistant', slugs)
        self.assertIn('communications-assistant', slugs)
        self.assertNotIn('us-only-assistant', slugs)
        self.assertNotIn('senior-research', slugs)

    @override_settings(SCOUT_RERANK_ENABLED=False, SCOUT_MULTISTEP_ENABLED=False, SCOUT_CLOSE_MATCHES=False)
    def test_failed_preparation_is_a_retryable_outcome(self):
        diagnostics = {'preparation_failed':True}
        Scout.get_matches_for_preferences(Scout._fallback_parse(self.brief), diagnostics=diagnostics)
        self.assertTrue(diagnostics['review_incomplete'])
        self.assertEqual(diagnostics['empty_reason'], 'review_incomplete')

    def test_incomplete_preview_has_same_brief_retry_and_loading_feedback(self):
        from django.template import engines
        from django.test import RequestFactory
        request = RequestFactory().get('/alerts/preview/?source=scout_test')
        request.user = SimpleNamespace(is_authenticated=True, is_staff=False)
        html = engines['django'].get_template('jobs/job_scout_preview.html').render({
            'intent':self.brief, 'preferences':{}, 'preview_jobs':[],
            'review_incomplete':True, 'user':request.user,
        }, request)
        self.assertIn('Retry this search', html)
        self.assertIn('data-scout-loading', html)
        self.assertIn('name="intent" value="'+self.brief+'"', html)
        self.assertNotIn('No eligible matches in this preview', html)
        self.assertNotIn('Adjust my search', html)

    def test_explicit_role_sector_qualifiers_remain_hard(self):
        for brief in ['Search for climate engineers.', 'Search for software engineer roles in education.']:
            prefs = Scout._fallback_parse(brief)
            if 'education' in brief:
                self.assertIn('education', prefs['required_category_slugs'])

    def test_including_contracts_does_not_narrow_an_open_search_to_contracts(self):
        prefs = Scout._fallback_parse('Find paid remote jobs. Include junior roles and short-term paid contracts.')
        self.assertEqual(prefs['job_types'], [])
        only = Scout._fallback_parse('Find remote contract roles. Include junior roles and short-term paid contracts.')
        self.assertEqual(only['job_types'], ['contract'])


from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@override_settings(SECURE_SSL_REDIRECT=False)
class ScoutIncompleteRetryBrowserTests(StaticLiveServerTestCase):
    def test_mobile_and_desktop_retry_keeps_brief_and_does_not_email(self):
        import os
        from pathlib import Path
        from urllib.parse import urlencode
        from playwright.sync_api import sync_playwright
        from jobs.tests.test_scout_results import setup_scout_fixture
        setup_scout_fixture(self)
        brief = 'Research Assistant or Communications Assistant roles from Pakistan.'
        folder = Path('.audit/scout-empty-fixes-2026-10-21')
        folder.mkdir(parents=True, exist_ok=True)
        for width in [390, 1280]:
            calls = []
            def search(intent, **kwargs):
                calls.append(intent)
                if len(calls) == 1:
                    kwargs['diagnostics'].update(review_incomplete=True, empty_reason='review_incomplete')
                    return self.prefs, []
                return self.prefs, [self.job]
            with patch.object(Scout, 'search_intent', side_effect=search), patch('jobs.services.email_service.email_service.send_job_alert') as mail, sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
                page = browser.new_page(viewport={'width':width, 'height':844})
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.live_server_url) else route.abort())
                page.goto(self.live_server_url + '/alerts/preview/?' + urlencode({'intent':brief,'source':'scout_retry_test'}))
                page.get_by_role('heading', name='Match review is temporarily incomplete').wait_for()
                page.screenshot(path=str(folder/f'retry-{width}.png'), full_page=True)
                page.get_by_role('button', name='Retry this search').first.click()
                page.get_by_role('heading', name=self.job.title).wait_for()
                self.assertEqual(calls, [brief, brief])
                mail.assert_not_called()
                browser.close()
