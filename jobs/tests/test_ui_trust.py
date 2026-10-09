"""Public UI trust boundaries, without provider, email or payment requests."""
from html.parser import HTMLParser
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase, RequestFactory


class UITrustTests(SimpleTestCase):
    def test_public_templates_render_and_persistent_labels_are_present(self):
        request = RequestFactory().get('/')
        request.user = SimpleNamespace(is_authenticated=True, is_staff=False, first_name='Test')
        context = {'can_create_alert': True, 'request': request, 'user': request.user, 'SITE_URL': 'https://remoteimpact.org', 'SITE_NAME': 'Remote Impact'}
        base = render_to_string('base.html', context)
        self.assertIn('for="newsletter-email"', base)
        self.assertIn('aria-label="Navigation menu"', base)
        self.assertIn('href="#main-content"', base)
        self.assertNotIn('POPUP_THRESHOLD_MS', base)
        listing = render_to_string('jobs/job_list.html', context)
        for identity in ['job-search', 'sort-select', 'filter-areas', 'filter-skills', 'filter-education', 'filter-experience', 'filter-countries', 'filter-role-types', 'filter-posted']:
            self.assertIn(f'for="{identity}"', listing)
            self.assertIn(f'id="{identity}"', listing)
        self.assertIn('aria-label="Minimum annual salary in USD"', listing)
        self.assertIn('aria-label="Maximum annual salary in USD"', listing)
        privacy = render_to_string('jobs/privacy.html', context)
        self.assertIn('DeepSeek', privacy)
        self.assertIn('Server-side account, checkout and job-alert events', privacy)
        home = render_to_string('jobs/home.html', context)
        self.assertIn('Avoid sensitive personal information', home)
        for template in ['jobs/job_alerts.html', 'jobs/job_alert_edit.html', 'jobs/job_scout_preview.html']:
            rendered = render_to_string(template, context)
            self.assertIn('Your brief may be sent to DeepSeek', rendered)
            self.assertNotIn('% match', rendered)

    def test_adversarial_filter_queries_remain_json_data(self):
        payload = '\" autofocus onfocus=alert(1) x-init=alert(2) </script><img src=x onerror=alert(3)>\' &'
        request = RequestFactory().get('/jobs/', {name: payload for name in ['category', 'skill', 'education', 'experience', 'country', 'type']})
        request.user = SimpleNamespace(is_authenticated=True, is_staff=False, first_name='Test')
        context = {
            'request': request, 'user': request.user,
            'current_categories': request.GET.getlist('category'),
            'current_types': request.GET.getlist('type'),
            'filters': {field: request.GET.getlist(query) for field, query in [('skills', 'skill'), ('educations', 'education'), ('experiences', 'experience'), ('countries', 'country')]},
            'skills_list': [{'slug': payload, 'display': payload, 'count': 1}],
        }
        rendered = render_to_string('jobs/job_list.html', context)

        class FilterMarkup(HTMLParser):
            def __init__(self):
                super().__init__()
                self.json = {}
                self.active = None
                self.attributes = []

            def handle_starttag(self, tag, attrs):
                self.attributes.extend(attrs)
                attributes = dict(attrs)
                if tag == 'script' and attributes.get('id', '').startswith('job-filter-'):
                    self.active = attributes['id']
                    self.json[self.active] = ''

            def handle_data(self, data):
                if self.active:
                    self.json[self.active] += data

            def handle_endtag(self, tag):
                if tag == 'script':
                    self.active = None

        markup = FilterMarkup()
        markup.feed(rendered)
        for name in ['areas', 'skills', 'education', 'experience', 'countries', 'role-types']:
            self.assertEqual(json.loads(markup.json['job-filter-' + name]), [payload])
        self.assertEqual(json.loads(markup.json['job-filter-skill-options']), context['skills_list'])
        for name, value in markup.attributes:
            self.assertNotIn('alert(', value or '', (name, value))
            self.assertNotIn(name, ['onfocus', 'onerror', 'autofocus'])
        self.assertNotIn('current_categories|safe', (Path(settings.BASE_DIR) / 'templates/jobs/job_list.html').read_text())

    def test_analytics_loads_and_strips_sensitive_fields(self):
        source = (Path(settings.BASE_DIR) / 'templates/base.html').read_text()
        self.assertIn('googletagmanager.com/gtag/js?id=G-H35R4HM1TG', source)
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for executable browser analytics guard tests')
        script = re.search(r'<script id="analytics-script">(.*?)</script>', source, re.S).group(1)
        harness = r'''
const assert = require('assert');
const vm = require('vm');
const handlers = {};
const document = {addEventListener: (name, cb) => handlers[name] = cb};
const window = {location: {origin: 'https://remoteimpact.org', pathname: '/scout/preview/', search: '?intent=private'}};
vm.runInNewContext(SCRIPT, {window, document, Date});
window.gtag('event', 'search', {event_category: 'engagement', event_label: 'private email', intent: 'secret'});
window.gtag('config', 'X', {page_path: '/secret'});
const serialized = JSON.stringify(window.dataLayer);
assert(!serialized.includes('private') && !serialized.includes('secret') && !serialized.includes('?intent'));
assert(serialized.includes('https://remoteimpact.org/scout/preview/'));
assert(serialized.includes('allow_ad_personalization_signals'));
'''.replace('SCRIPT', json.dumps(script))
        result = subprocess.run([node, '-e', harness], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
