from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase

from jobs.csp import digest, policy_hashes, ScriptHashes
from jobs.middleware import SecurityHeadersMiddleware
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import path
from jobboard.urls import urlpatterns as application_urls
import os


def proof_page(request):
    authored = Template('{% load csp %}{% csp_script %}<script>document.body.dataset.allowed="yes";</script>{% endcsp_script %}').render(Context())
    return HttpResponse('<!doctype html><body>' + authored + '<script>document.body.dataset.untrusted="executed";</script></body>')


urlpatterns = [path('csp-proof/', proof_page), *application_urls]


class AuthoredScriptPolicyTests(SimpleTestCase):
    def render(self, content):
        return Template('{% load csp %}' + content).render(Context())

    def test_authored_script_is_allowed_and_untrusted_script_is_not(self):
        code = 'window.cspAuditTrusted=1;'
        rendered = self.render('{% csp_script %}<script>' + code + '</script>{% endcsp_script %}')
        scripts, _ = policy_hashes(rendered + '<script>window.cspAuditUntrusted=1;</script>')
        self.assertIn(digest(code), scripts)
        self.assertNotIn(digest('window.cspAuditUntrusted=1;'), scripts)

    def test_copying_a_signature_onto_different_code_is_rejected(self):
        good = self.render('{% csp_script %}<script>window.cspMarker=1;</script>{% endcsp_script %}')
        altered = good.replace('cspMarker=1', 'cspMarker=2')
        parser = ScriptHashes(); parser.feed(altered)
        self.assertEqual(parser.scripts, set())

    def test_only_signed_handler_attributes_are_authorized(self):
        code = 'return confirm("Audit-only test");'
        rendered = self.render('<form {% csp_handler "onsubmit" %}' + code + '{% endcsp_handler %}></form>')
        _, handlers = policy_hashes(rendered)
        self.assertIn(digest(code), handlers)
        parser = ScriptHashes(); parser.feed('<form onsubmit="window.cspAuditBad=1"></form>')
        self.assertEqual(parser.handlers, set())

    def test_cached_authorization_is_bound_to_exact_content(self):
        html = self.render('{% csp_script %}<script>window.cspCached=1;</script>{% endcsp_script %}')
        self.assertEqual(policy_hashes(html), policy_hashes(html))
        self.assertNotIn(digest('window.cspCached=2;'), policy_hashes(html.replace('Cached=1','Cached=2'))[0])

    def test_component_compiles_to_registered_factory_without_eval(self):
        html = self.render('{% csp_component %}<div x-data="{open:false, init(){this.open=true;}}">{% endcsp_component %}')
        self.assertIn('Alpine.data(', html)
        self.assertIn('x-data="ri_', html)
        parser = ScriptHashes(); parser.feed(html)
        self.assertEqual(len(parser.scripts), 1)

    def test_enforced_script_policy_contains_neither_unsafe_flag(self):
        html = self.render('{% csp_script %}<script>window.cspPolicy=1;</script>{% endcsp_script %}')
        response = SecurityHeadersMiddleware(lambda request: HttpResponse(html))(RequestFactory().get('/'))
        policy = response['Content-Security-Policy']
        script = next(p for p in policy.split(';') if p.strip().startswith('script-src '))
        self.assertNotIn('unsafe-inline', script)
        self.assertNotIn('unsafe-eval', script)
        self.assertIn(digest('window.cspPolicy=1;'), script)


@override_settings(SECURE_SSL_REDIRECT=False, ROOT_URLCONF='jobs.tests.test_csp')
class StrictPolicyBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        from jobs.tests.test_scout_results import setup_scout_fixture
        setup_scout_fixture(self)

    def test_parser_enforces_signed_script_boundary(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as p:
            args = {'executable_path':os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE']} if os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') else {}
            browser = p.chromium.launch(headless=True, **args)
            page = browser.new_page()
            response = page.goto(self.live_server_url + '/csp-proof/')
            self.assertEqual(response.status, 200)
            expect(page.locator('body')).to_have_attribute('data-allowed', 'yes')
            self.assertIsNone(page.locator('body').get_attribute('data-untrusted'))
            browser.close()

    def test_public_components_initialize_with_csp_enforced(self):
        from playwright.sync_api import sync_playwright, expect
        pages = ['/', '/jobs/', '/tools/salary-to-hourly-calculator/', '/tools/time-zone-overlap-finder/',
                 '/tools/word-counter/', '/tools/pomodoro-timer/', '/tools/freelance-rate-calculator/',
                 '/tools/salary-negotiation-script/', '/tools/skills-gap-analyzer/', '/tools/pto-calculator/',
                 '/tools/thank-you-email-generator/', '/tools/pay-raise-calculator/', '/tools/remote-work-readiness-quiz/',
                 '/tools/cost-of-living-comparison/', '/tools/resignation-letter-generator/',
                 '/tools/job-description-generator/', '/tools/interview-prep/', '/tools/cover-letter-generator/',
                 '/accounts/login/', '/pricing/', '/jobs/post/']
        with sync_playwright() as p:
            args = {'executable_path':os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE']} if os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') else {}
            browser = p.chromium.launch(headless=True, **args)
            ctx = browser.new_context()
            ctx.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.live_server_url) else route.abort())
            page = ctx.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: errors.append(message.text) if message.type == 'error' and ('Alpine' in message.text or 'Content Security Policy' in message.text) else None)
            for path in pages:
                errors.clear()
                response = page.goto(self.live_server_url + path)
                page.wait_for_load_state('networkidle')
                self.assertEqual(response.status, 200, path)
                if path == '/tools/word-counter/':
                    page.locator('textarea').fill('A useful remote career')
                    expect(page.locator('[x-text="stat.value"]').first).to_have_text('4')
                    page.get_by_role('button', name='Clear text', exact=True).click()
                    expect(page.locator('textarea')).to_have_value('')
                self.assertEqual(errors, [], path + ': ' + '\n'.join(errors[:3]))
            browser.close()
