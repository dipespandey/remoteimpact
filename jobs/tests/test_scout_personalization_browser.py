import os
from pathlib import Path
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from jobs.tests.test_scout_results import setup_scout_fixture
from jobs.models import ConsumerFunnelEvent as E,JobAlert,JobAlertDelivery
from jobs.services.job_scout_service import JobScoutService


@override_settings(SECURE_SSL_REDIRECT=False,ACCOUNT_RATE_LIMITS={})
class ScoutPersonalizationBrowserTests(StaticLiveServerTestCase):
    def test_mobile_sample_personalize_signup_confirm_and_refresh(self):
        from playwright.sync_api import sync_playwright
        setup_scout_fixture(self)
        folder=Path('.audit/scout-funnel-build-2026-10-16');folder.mkdir(parents=True,exist_ok=True)
        intent='Remote Python engineering roles in Canada at climate organizations, no internships.'
        with patch('jobs.services.email_service.email_service.send_email',return_value=True),sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
            ctx=browser.new_context(viewport={'width':390,'height':844}, user_agent='Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36')
            ctx.route('**/*',lambda route:route.continue_() if route.request.url.startswith(self.live_server_url) else route.abort())
            page=ctx.new_page()
            with patch.object(JobScoutService,'search_intent',return_value=(self.prefs,[self.job])):
                page.goto(self.live_server_url)
                page.get_by_role('link',name='Python in Europe').click()
                flow=page.locator('#personalize-brief [name="scout_flow"]').input_value()
                page.screenshot(path=str(folder/'sample-mobile.png'),full_page=True)
                page.get_by_role('link',name='Personalize this search',exact=True).click()
                page.locator('#personalized-intent').fill(intent)
                page.get_by_role('button',name='Preview my search',exact=True).click()
                page.get_by_role('heading',name='Your job matches').wait_for()
                self.assertEqual(page.locator('#personalize-brief [name="scout_flow"]').input_value(),flow)
                self.assertEqual(page.locator('#personalized-intent').input_value(),intent)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
                self.assertFalse(page.locator('details').first.evaluate('(el) => el.open'))
                result = page.get_by_role('heading', name=self.job.title).bounding_box()
                signup = page.get_by_role('heading', name='Email your matches').bounding_box()
                self.assertLess(result['y'], signup['y'])
                mobile_next = page.get_by_role('link', name='Email my matches', exact=True)
                self.assertTrue(mobile_next.is_visible())
                self.assertLess(mobile_next.bounding_box()['y'] + mobile_next.bounding_box()['height'], 844)
                self.assertIn('next=', mobile_next.get_attribute('href'))
                self.assertEqual(page.locator('#preview-consider-heading').count(), 0)
                page.screenshot(path=str(folder/'personalized-mobile.png'),full_page=True)
                mobile_next.click()
                page.locator('#id_email').fill('personalized-browser@example.org')
                page.locator('[name="password1"]').fill('test-personalized-password-531!')
                page.locator('[name="password2"]').fill('test-personalized-password-531!')
                page.get_by_role('button',name='Continue to my Scout').click()
                page.wait_for_url('**/alerts/?**')
            self.assertEqual(page.locator('[name="intent_text"]').input_value(),intent)
            self.assertEqual(page.locator('[name="scout_flow"]').input_value(),flow)
            page.get_by_role('heading',name='Confirm your search').wait_for()
            page.screenshot(path=str(folder/'confirmation-mobile.png'),full_page=True)
            with patch.object(JobScoutService,'parse_intent',return_value=self.prefs),patch.object(JobScoutService,'get_matches',return_value=[self.job]) as matches,patch('jobs.views.job_alerts.email_service.send_job_alert',return_value=True) as mail:
                page.get_by_role('button',name='Find and email my matches').click()
                page.wait_for_url('**/alerts/?created=*')
                page.reload()
                page.get_by_text(self.job.title,exact=True).first.wait_for()
                self.assertEqual(matches.call_count,1);self.assertEqual(mail.call_count,1)
            results_url = page.url
            result_link = page.get_by_role('link', name=self.job.title, exact=True)
            self.assertIn('scout_delivery=', result_link.get_attribute('href'))
            with page.expect_response(lambda response: '/v/?scout_delivery=' in response.url and response.request.method == 'POST') as viewed:
                result_link.click()
            self.assertEqual(viewed.value.status, 204)
            with ctx.expect_event('response', predicate=lambda response: '/go/?scout_delivery=' in response.url) as applied, ctx.expect_page() as application:
                page.get_by_role('link', name='Apply on company site', exact=True).first.click()
            self.assertEqual(applied.value.status, 302)
            self.assertTrue(applied.value.headers['location'].startswith('https://example.org/apply'))
            application.value.close()
            page.goto(results_url)
            useful = page.get_by_role('button', name='Mark as a useful match', exact=True)
            useful.click()
            page.wait_for_url('**/alerts/?created=*')
            self.assertEqual(useful.get_attribute('aria-pressed'), 'true')
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
            page.screenshot(path=str(folder/'results-feedback-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1280,'height':900})
            with patch.object(JobScoutService,'search_intent',return_value=(self.prefs,[self.job])):
                page.goto(self.live_server_url+'/alerts/preview/?intent=Remote+Python+engineering+roles+in+Europe.&source=home_hero_example_python')
                page.screenshot(path=str(folder/'sample-desktop.png'),full_page=True)
            browser.close()
        for event in [E.Event.SCOUT_PREVIEWED,E.Event.SCOUT_PERSONALIZED,E.Event.SCOUT_SIGNUP_STARTED,E.Event.ACCOUNT_CREATED,E.Event.SCOUT_FORM_VIEWED,E.Event.SCOUT_SUBMIT_STARTED,E.Event.SCOUT_CREATED,E.Event.SCOUT_SUBMIT_RESULT]:
            self.assertTrue(E.objects.filter(event=event,metadata__scout_flow_id=flow).exists(),event)
        self.assertEqual(JobAlert.objects.count(),1)
        delivery = JobAlertDelivery.objects.get()
        self.assertEqual(delivery.feedback, JobAlertDelivery.Feedback.RELEVANT)
        for event in [E.Event.SCOUT_JOB_VIEWED, E.Event.SCOUT_APPLY_CLICKED, E.Event.MATCH_RELEVANT]:
            self.assertEqual(E.objects.filter(event=event,metadata__delivery_id=delivery.pk).count(),1,event)
        import json
        from io import StringIO
        from django.core.management import call_command
        stream=StringIO();call_command('scout_funnel_report',days=1,json=True,stdout=stream)
        report=json.loads(stream.getvalue())
        self.assertEqual(report['by_entry_state']['anonymous']['stage_flows']['account_created'],1)
        self.assertEqual(report['by_entry_state']['anonymous']['stage_flows']['scout_created'],1)
        self.assertEqual(report['by_entry_state']['authenticated']['stage_flows']['account_created'],0)
        (folder/'synthetic-cohort-example.json').write_text(json.dumps({'synthetic_test_fixture':True,'report':json.loads(stream.getvalue())},indent=2)+'\n')

    def test_loading_feedback_is_visible_while_navigation_is_pending(self):
        from playwright.sync_api import sync_playwright, expect
        from django.contrib.auth import get_user_model
        setup_scout_fixture(self)
        user = get_user_model().objects.create_user('spinner-user', 'spinner@example.org', 'test-pass-531!')
        self.client.force_login(user)
        folder = Path('.audit/scout-deeper-2026-10-16'); folder.mkdir(exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
            ctx = browser.new_context(viewport={'width':390, 'height':844})
            ctx.add_cookies([{'name':'sessionid', 'value':self.client.cookies['sessionid'].value, 'url':self.live_server_url}])
            ctx.route('**/*', lambda route:route.continue_() if route.request.url.startswith(self.live_server_url) else route.abort())
            page = ctx.new_page()
            # Prevent navigation after the real loading listener has handled
            # the event, so the waiting state can be inspected without sleeps.
            for kind in ['sample', 'composer', 'edit', 'create']:
                if kind in ['sample', 'composer']:
                    page.goto(self.live_server_url)
                elif kind == 'edit':
                    with patch.object(JobScoutService, 'search_intent', return_value=(self.prefs,[self.job])):
                        page.goto(self.live_server_url+'/alerts/preview/?intent=Remote+Python+engineering+roles+in+Europe&source=home_hero_example_python')
                else:
                    page.goto(self.live_server_url+'/alerts/')
                page.evaluate("""() => {
                    window.preventedScoutNavigations = 0;
                    document.addEventListener('click', e => {
                        if (e.target.closest('a[data-scout-loading]')) { if (!e.defaultPrevented) window.preventedScoutNavigations++; e.preventDefault(); }
                    });
                    document.addEventListener('submit', e => {
                        if (e.target.matches('form[data-scout-loading]')) { if (!e.defaultPrevented) window.preventedScoutNavigations++; e.preventDefault(); }
                    });
                }""")
                if kind == 'sample':
                    control = page.locator('[data-track=home_scout_example_python]')
                elif kind == 'composer':
                    page.locator('#hero-job-scout-launcher').click()
                    page.locator('#job-scout-composer-form button[type="submit"]').click()
                    self.assertEqual(page.locator('.scout-loading-spinner').count(), 0)
                    page.locator('#job-scout-composer-intent').fill(self.intent)
                    control = page.locator('#job-scout-composer-form button[type="submit"]')
                elif kind == 'edit':
                    page.locator('#scout-brief-editor > summary').click()
                    control = page.locator('#personalize-brief button[type="submit"]')
                else:
                    page.locator('[name="intent_text"]').fill(self.intent)
                    control = page.locator('form[data-scout-loading] button[type=submit]')
                page.emulate_media(reduced_motion='reduce' if kind == 'edit' else 'no-preference')
                handle = control.element_handle()
                control.click(no_wait_after=True)
                self.assertEqual(handle.get_attribute('aria-busy'), 'true')
                self.assertTrue(handle.query_selector('.scout-loading-spinner').is_visible())
                if kind == 'edit':
                    self.assertEqual(handle.query_selector('.scout-loading-spinner').evaluate('(el) => getComputedStyle(el).animationName'), 'none')
                self.assertIn('Matching roles', page.evaluate("document.getElementById('scout-loading-status').textContent"))
                page.screenshot(path=str(folder/f'loading-{kind}-mobile.png'))
                if kind == 'sample':
                    handle.evaluate('(el) => el.click()')
                    self.assertEqual(page.evaluate('window.preventedScoutNavigations'), 1)
                    self.assertEqual(len(handle.query_selector_all('.scout-loading-spinner')), 1)
                # Restoring from history must restore controls as well.
                page.evaluate("window.dispatchEvent(new Event('pageshow'))")
                self.assertIsNone(handle.get_attribute('aria-busy'))
                self.assertFalse(handle.is_disabled())
                self.assertEqual(page.evaluate('window.preventedScoutNavigations'),1)
            browser.close()

    def test_compact_sample_cards_and_editor_at_phone_tablet_desktop_widths(self):
        import copy
        from urllib.parse import urlencode
        from playwright.sync_api import sync_playwright, expect
        setup_scout_fixture(self)
        self.job.title = 'Communications Manager, Development and Communications'
        evidence = 'Relevant evidence: Lead strategic communications and thought leadership for a global school health nonprofit, managing publications, media engagement, and brand messaging.'
        self.job.scout_reasons = [evidence, 'Published recently', 'Uses Global Health']
        other = copy.copy(self.job)
        other.slug = 'communications-officer'; other.title = 'Senior Communications Officer, Editorial and Production'
        other.scout_reasons = [evidence, 'Check: confirm hiring location']
        other.scout_needs_confirmation = True
        folder = Path('.audit/scout-compact-fix-2026-10-16'); folder.mkdir(exist_ok=True)
        intent = 'Remote communications roles at global health nonprofits, full-time.'
        url = self.live_server_url+'/alerts/preview/?'+urlencode({'intent':intent, 'source':'home_hero_example_health'})
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
            ctx = browser.new_context()
            ctx.route('**/*', lambda route:route.continue_() if route.request.url.startswith(self.live_server_url) else route.abort())
            page = ctx.new_page()
            for width in [360,390,768,1440]:
                page.set_viewport_size({'width':width, 'height':900 if width>640 else 844})
                with patch.object(JobScoutService, 'search_intent', return_value=(self.prefs,[self.job,other])):
                    page.goto(url)
                editor = page.locator('#scout-brief-editor')
                self.assertFalse(editor.evaluate('(el) => el.open'))
                self.assertLess(editor.bounding_box()['height'],125)
                cards = page.locator('.scout-preview-card')
                self.assertEqual(cards.count(),2)
                first = cards.first.bounding_box()
                self.assertLess(first['height'],240)
                if width<640:
                    self.assertLess(first['y'],600)
                title = cards.first.locator('.job-title').bounding_box()
                self.assertGreaterEqual(title['width'],first['width']-45)
                expect(page.get_by_text('Check: confirm hiring location',exact=True)).to_be_visible()
                self.assertFalse(cards.first.get_by_text(evidence,exact=True).is_visible())
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width)
                page.screenshot(path=str(folder/f'compact-{width}.png'),full_page=True)
                cards.first.get_by_text('Why this matched',exact=True).first.click()
                expect(cards.first.get_by_text(evidence,exact=True)).to_be_visible()
                page.get_by_role('link',name='Personalize this search',exact=True).click()
                self.assertTrue(editor.evaluate('(el) => el.open'))
                textarea = page.locator('#personalized-intent')
                expect(textarea).to_be_focused()
                self.assertGreaterEqual(textarea.bounding_box()['height'],90)
                self.assertLessEqual(textarea.bounding_box()['height'],110)
                self.assertEqual(textarea.input_value(),intent)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width)
                page.screenshot(path=str(folder/f'editor-{width}.png'),full_page=True)
            browser.close()
