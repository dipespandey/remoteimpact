import json
import re
import shutil
import subprocess
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import ConsumerFunnelEvent as E, Job, JobAlert, JobAlertDelivery, JobDailyStat
from jobs.services.scout_usefulness import build_scout_usefulness
from jobs.tests.test_scout_results import setup_scout_fixture

BROWSER = 'Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Safari/605.1.15'


@override_settings(SECURE_SSL_REDIRECT=False, ACCOUNT_RATE_LIMITS={})
class ScoutEngagementTests(TestCase):
    def setUp(self):
        cache.clear()
        setup_scout_fixture(self)
        self.user = get_user_model().objects.create_user('measurement-owner')
        self.client.force_login(self.user)
        self.alert = JobAlert.objects.create(user=self.user, intent_text='PRIVATE BRIEF')
        self.delivery = JobAlertDelivery.objects.create(alert=self.alert, job=self.job)

    def beacon(self, reference=None, **headers):
        reference = self.delivery.pk if reference is None else reference
        return self.client.post(reverse('jobs:job_view_beacon', args=[self.job.slug]) + f'?scout_delivery={reference}',
            HTTP_USER_AGENT=headers.pop('HTTP_USER_AGENT', BROWSER), **headers)

    def apply(self, reference=None, **headers):
        reference = self.delivery.pk if reference is None else reference
        return self.client.get(reverse('jobs:job_apply_redirect', args=[self.job.slug]), {'scout_delivery': reference},
            HTTP_USER_AGENT=headers.pop('HTTP_USER_AGENT', BROWSER), **headers)

    def test_get_is_not_an_open_and_owner_links_carry_retained_context(self):
        page = self.client.get(self.job.get_absolute_url(), {'source': 'scout_email', 'alert_id': self.alert.pk}, HTTP_USER_AGENT=BROWSER)
        self.assertEqual(page.status_code, 200)
        self.assertFalse(E.objects.filter(event=E.Event.SCOUT_JOB_VIEWED).exists())
        self.assertContains(page, f'/v/?scout_delivery={self.delivery.pk}')
        self.assertContains(page, f'/go/?scout_delivery={self.delivery.pk}')

    def test_visible_beacon_and_apply_dedupe_without_losing_redirect(self):
        for _ in range(2):
            self.assertEqual(self.beacon().status_code, 204)
            response = self.apply()
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith('https://example.org/apply?'))
            self.assertNotIn('scout_delivery', response.url)
        self.assertEqual(E.objects.filter(event=E.Event.SCOUT_JOB_VIEWED, alert=self.alert).count(), 1)
        self.assertEqual(E.objects.filter(event=E.Event.SCOUT_APPLY_CLICKED, alert=self.alert).count(), 1)
        self.assertEqual(JobDailyStat.objects.get(job=self.job).apply_clicks, 1)

    def test_apply_before_view_redirects_but_does_not_claim_verified_engagement(self):
        self.assertEqual(self.apply().status_code, 302)
        self.assertFalse(E.objects.filter(event=E.Event.SCOUT_APPLY_CLICKED).exists())

    def test_cache_outage_cannot_invent_a_scout_open_before_apply(self):
        with patch('jobs.services.job_stats.cache.get', side_effect=RuntimeError('cache offline')):
            self.assertEqual(self.apply().status_code, 302)
        self.assertFalse(E.objects.filter(event=E.Event.SCOUT_APPLY_CLICKED).exists())

    def test_forged_reference_wrong_job_owner_logged_out_and_bot_are_not_attributed(self):
        with patch('jobs.services.embedding_service.embed_job', return_value=None):
            other_job = Job.objects.create(title='Different role', slug='different-measurement-role', organization=self.job.organization)
        wrong_job = JobAlertDelivery.objects.create(alert=self.alert, job=other_job)
        for invalid in (0, self.delivery.pk + 1000, wrong_job.pk, 'x', '9' * 40, '9223372036854775808', '١'):
            self.beacon(invalid)
            self.apply(invalid)
        self.beacon(HTTP_USER_AGENT='Googlebot/2.1')
        self.apply(HTTP_USER_AGENT='Googlebot/2.1')
        other = get_user_model().objects.create_user('measurement-other')
        self.client.force_login(other)
        self.beacon()
        self.apply()
        self.client.logout()
        self.beacon()
        self.apply()
        self.assertFalse(E.objects.filter(event__in=(E.Event.SCOUT_JOB_VIEWED, E.Event.SCOUT_APPLY_CLICKED)).exists())

    def test_email_source_requires_retained_job_not_just_an_owned_scout(self):
        self.delivery.delete()
        page = self.client.get(self.job.get_absolute_url(), {'source': 'scout_email', 'alert_id': self.alert.pk})
        self.assertNotContains(page, '?scout_delivery=')
        self.client.post(reverse('jobs:job_view_beacon', args=[self.job.slug]) + f'?source=scout_email&alert_id={self.alert.pk}', HTTP_USER_AGENT=BROWSER)
        self.assertFalse(E.objects.filter(event=E.Event.SCOUT_JOB_VIEWED).exists())

    def test_changing_feedback_reports_only_the_latest_explicit_answer(self):
        for choice in ('relevant', 'not_relevant', 'relevant'):
            response = self.client.post(reverse('jobs:job_alert_feedback', args=[self.delivery.pk]), {'feedback': choice})
            self.assertEqual(response.status_code, 302)
        report = build_scout_usefulness(self.alert.created_at - timedelta(seconds=1), timezone.now())
        self.assertEqual(E.objects.filter(alert=self.alert, event__in=(E.Event.MATCH_RELEVANT, E.Event.MATCH_NOT_RELEVANT)).count(), 2)
        self.assertEqual(report['totals']['latest_explicit_feedback'], {'relevant': 1, 'not_relevant': 0, 'unrated': 0, 'unknown_as_of_window_end': 0})
        self.assertEqual(report['totals']['accounts_marking_at_least_one_match_relevant'], 1)


class ScoutUsefulnessReportTests(TestCase):
    def setUp(self):
        setup_scout_fixture(self)
        self.user = get_user_model().objects.create_user('report-owner', email='private@example.test')
        self.alert = JobAlert.objects.create(user=self.user, intent_text='PRIVATE BRIEF')
        self.delivery = JobAlertDelivery.objects.create(alert=self.alert, job=self.job)
        self.since = timezone.now() - timedelta(days=1)

    def event(self, event, **metadata):
        return E.objects.create(event=event, user=self.user, alert=self.alert, metadata=metadata)

    def report(self, until=None):
        return build_scout_usefulness(self.since, until or timezone.now())

    def test_retained_matches_are_separate_from_emails_unverified_opens_and_latest_feedback(self):
        flow = 'a' * 32
        self.event(E.Event.SCOUT_CREATED, scout_flow_id=flow, scout_source='home_hero_example_python')
        E.objects.create(event=E.Event.SCOUT_PREVIEWED, metadata={'scout_flow_id': flow})
        self.event(E.Event.INITIAL_EMAIL_SENT)
        self.event(E.Event.INITIAL_EMAIL_SENT)
        self.event(E.Event.SCOUT_JOB_VIEWED, job_id=self.job.pk, source='scout_email')
        self.event(E.Event.MATCH_RELEVANT, delivery_id=self.delivery.pk)
        self.event(E.Event.MATCH_NOT_RELEVANT, delivery_id=self.delivery.pk)
        self.delivery.feedback = 'not_relevant'
        self.delivery.feedback_at = timezone.now()
        self.delivery.save(update_fields=['feedback', 'feedback_at'])
        report = self.report()
        self.assertEqual(report['totals']['retained_delivery_records'], 1)
        self.assertEqual(report['totals']['scouts_with_linked_preview'], 1)
        self.assertEqual(report['totals']['distinct_deliveries_opened'], 0)
        self.assertEqual(report['legacy_or_unverified_job_view_events'], 1)
        self.assertEqual(report['totals']['latest_explicit_feedback'], {'relevant': 0, 'not_relevant': 1, 'unrated': 0, 'unknown_as_of_window_end': 0})
        self.assertEqual(report['totals']['relevant_share_of_rated_matches'], 0)
        for private in (flow, self.user.email, self.alert.intent_text):
            self.assertNotIn(private, json.dumps(report))

    def test_validated_evidence_requires_retained_delivery_owner_job_and_time(self):
        self.event(E.Event.SCOUT_JOB_VIEWED, delivery_id=self.delivery.pk, job_id=self.job.pk,
            tracking_version=1, signal='owner_delivery_beacon')
        self.event(E.Event.SCOUT_JOB_VIEWED, delivery_id=self.delivery.pk, job_id=self.job.pk,
            tracking_version=1, signal='owner_delivery_beacon')
        self.event(E.Event.SCOUT_APPLY_CLICKED, delivery_id=self.delivery.pk, job_id=self.job.pk,
            tracking_version=1, signal='owner_delivery_apply_redirect')
        self.event(E.Event.SCOUT_JOB_VIEWED, delivery_id=self.delivery.pk, job_id=self.job.pk + 10,
            tracking_version=1, signal='owner_delivery_beacon')
        invalid = self.event(E.Event.SCOUT_JOB_VIEWED, delivery_id=self.delivery.pk, job_id=self.job.pk,
            tracking_version=1, signal='owner_delivery_beacon')
        E.objects.filter(pk=invalid.pk).update(created_at=self.alert.created_at + (self.delivery.sent_at - self.alert.created_at) / 2)
        report = self.report()
        self.assertEqual(report['totals']['distinct_deliveries_opened'], 1)
        self.assertEqual(report['totals']['distinct_deliveries_with_apply_click'], 1)
        self.assertEqual(report['totals']['accounts_with_verified_job_open'], 1)
        self.assertEqual(report['legacy_or_unverified_job_view_events'], 2)

    def test_feedback_after_window_end_remains_unknown_and_empty_denominator_is_null(self):
        until = timezone.now()
        self.delivery.feedback = 'relevant'
        self.delivery.feedback_at = until + timedelta(seconds=1)
        self.delivery.save(update_fields=['feedback', 'feedback_at'])
        totals = self.report(until)['totals']
        self.assertEqual(totals['latest_explicit_feedback']['unknown_as_of_window_end'], 1)
        self.assertIsNone(totals['relevant_share_of_rated_matches'])

    def test_command_is_read_only_source_redacted_and_known_internal_excluded(self):
        self.event(E.Event.SCOUT_CREATED, source=self.user.email)
        before = (E.objects.count(), JobAlert.objects.count(), JobAlertDelivery.objects.count())
        output = StringIO()
        with patch('jobs.services.email_service.email_service.send_email', side_effect=AssertionError('report sent email')):
            call_command('scout_usefulness_report', days=1, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(set(report['by_scout_entry_source']), {'Redacted label'})
        self.assertEqual(before, (E.objects.count(), JobAlert.objects.count(), JobAlertDelivery.objects.count()))
        with override_settings(GROWTH_INTERNAL_USER_IDS=[self.user.pk]):
            self.assertEqual(self.report()['totals']['scouts'], 0)
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.report()['totals']['scouts'], 0)

    def test_admin_shows_separate_evidence_without_exposing_private_match_data(self):
        operator = get_user_model().objects.create_user('measurement-operator', is_staff=True)
        operator.user_permissions.add(*Permission.objects.filter(content_type__app_label='jobs',
            codename__in=('view_consumerfunnelevent', 'view_employerfunnelevent')))
        self.delivery.feedback = 'relevant'
        self.delivery.feedback_at = timezone.now()
        self.delivery.save(update_fields=['feedback', 'feedback_at'])
        self.client.force_login(operator)
        response = self.client.get(reverse('admin_diagnostics'))
        self.assertEqual(response.status_code, 200)
        for heading in ('Are the matches useful?', 'Scouts with retained matches', 'Latest answer: useful', 'Apply clicks are not completed applications'):
            self.assertContains(response, heading)
        self.assertEqual(response.context['a']['usefulness']['totals']['latest_explicit_feedback']['relevant'], 1)
        for private in (self.user.email, self.alert.intent_text):
            self.assertNotContains(response, private)


class AnalyticsAttributionPrivacyTests(SimpleTestCase):
    def test_runtime_config_strips_delivery_alert_brief_and_referrer_tokens(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required to execute the browser analytics guard')
        source = (Path(settings.BASE_DIR) / 'templates/base.html').read_text()
        script = re.search(r'<script id="analytics-script">(.*?)</script>', source, re.S).group(1)
        harness = r'''
const assert = require('assert');
const vm = require('vm');
const document = {referrer: 'https://remoteimpact.org/accounts/confirm-email/secret-token/?alert_id=918&scout_delivery=725&intent=PRIVATE_BRIEF#private-hash', addEventListener: () => {}};
const window = {location: {origin: 'https://remoteimpact.org', pathname: '/jobs/example/', search: '?scout_delivery=725&alert_id=918&intent=PRIVATE_BRIEF', hash: '#private-hash'}};
vm.runInNewContext(SCRIPT, {window, document, Date, URL});
window.gtag('event', 'search', {event_label: 'PRIVATE_BRIEF', source_link: document.referrer, delivery_id: 725});
const serialized = JSON.stringify(window.dataLayer);
for (const secret of ['secret-token', '918', '725', 'PRIVATE_BRIEF', 'private-hash', 'source_link', 'delivery_id']) assert(!serialized.includes(secret), secret);
const config = Array.from(window.dataLayer.find(args => args[0] === 'config'))[2];
assert.strictEqual(config.page_location, 'https://remoteimpact.org/jobs/example/');
assert.strictEqual(config.page_referrer, 'https://remoteimpact.org');
'''.replace('SCRIPT', json.dumps(script))
        result = subprocess.run([node, '-e', harness], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
