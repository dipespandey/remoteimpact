from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import ConsumerFunnelEvent as C, EmployerFunnelEvent as E, JobAlert
from jobs.services.product_analytics import build_product_analytics, label, read_scope, scope_token


class ProductAnalyticsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('customer', email='secret-person@example.test')
        self.staff = get_user_model().objects.create_user('operator', is_staff=True)
        self.alert = JobAlert.objects.create(user=self.user, intent_text='PRIVATE SCOUT BRIEF')

    def event(self, event, **kwargs):
        return C.objects.create(event=event, user=kwargs.pop('user', self.user),
                                alert=kwargs.pop('alert', self.alert), **kwargs)

    def test_cohort_deduplicates_and_uses_linked_alerts(self):
        self.event('initial_email_sent')
        self.event('initial_email_sent')
        other = JobAlert.objects.create(user=self.user)
        self.event('initial_email_sent', alert=other)
        report = build_product_analytics()
        self.assertEqual([r['count'] for r in report['cohort']], [1, 1, 1])
        self.assertEqual(report['outcomes']['sent'], 2)
        self.assertEqual(report['cohort'][-1]['percent'], 100)

    def test_pre_signup_and_wrong_owner_email_not_in_activation(self):
        self.event('initial_email_sent', user=self.staff)
        early = self.event('initial_email_sent')
        C.objects.filter(pk=early.pk).update(created_at=self.alert.created_at - timedelta(minutes=1))
        report = build_product_analytics()
        self.assertEqual(report['cohort'][-1]['count'], 0)

    def test_outcomes_are_disjoint_and_retry_resolves_failure(self):
        self.event('initial_email_failed')
        self.event('initial_email_sent')
        empty = JobAlert.objects.create(user=self.user)
        self.event('initial_matches_empty', alert=empty)
        failed = JobAlert.objects.create(user=self.user)
        self.event('initial_email_failed', alert=failed)
        JobAlert.objects.create(user=self.user)
        outcomes = build_product_analytics()['outcomes']
        self.assertEqual(outcomes, {'total': 4, 'sent': 1, 'empty': 1, 'failed': 1, 'unknown': 1})

    @override_settings(GROWTH_INTERNAL_USER_IDS=[99999])
    def test_staff_internal_and_future_events_are_excluded(self):
        self.event('checkout_failed', user=self.staff)
        future = self.event('checkout_failed')
        C.objects.filter(pk=future.pk).update(created_at=timezone.now() + timedelta(days=1))
        with override_settings(GROWTH_INTERNAL_USER_IDS=[self.user.pk]):
            report = build_product_analytics()
        self.assertEqual(report['summary'][0]['count'], 0)
        self.assertEqual(report['cohort'][0]['count'], 0)

    def test_campaigns_separate_trials_requests_and_unknown_sources(self):
        E.objects.create(event='feature_page_requested', metadata={'source': 'outreach'})
        self.event('payment_completed', metadata={'trial': True, 'source': 'launch'})
        self.event('payment_completed', metadata={'campaign_labels': {'utm_source': 'newsletter', 'utm_campaign': 'october'}})
        self.event('scout_created', metadata={'source': 'secret-person@example.test', 'brief': 'PRIVATE SCOUT BRIEF'})
        report = build_product_analytics()
        by_source = {r['source']: r for r in report['campaigns']}
        self.assertEqual(by_source['launch']['trial'], 1)
        self.assertEqual(by_source['newsletter']['completions'], 1)
        self.assertEqual(by_source['outreach']['requests'], 1)
        self.assertIn('Redacted label', by_source)
        self.assertNotIn('PRIVATE SCOUT BRIEF', str(report))
        self.assertNotIn('secret-person@example.test', str(report))

    def test_failures_do_not_include_created_or_empty_outcomes(self):
        for result in ('created', 'invalid', 'error', 'blocked'):
            self.event('scout_submit_result', metadata={'outcome': result})
        self.event('initial_matches_empty')
        self.assertEqual(sum(row['count'] for row in build_product_analytics()['failures']), 3)
        self.assertEqual(build_product_analytics(failures_only=True)['timeline_total'], 3)

    def test_flow_links_include_anonymous_stages_without_cross_flow_leaks(self):
        flow = 'a' * 32
        self.event('scout_previewed', user=None, alert=None, metadata={'scout_flow_id': flow})
        self.event('scout_created', metadata={'scout_flow_id': flow})
        self.event('scout_created', metadata={'scout_flow_id': 'b' * 32})
        token = scope_token('flow', flow)
        report = build_product_analytics(scope=read_scope(token))
        self.assertEqual(report['timeline_total'], 2)
        self.assertEqual(len({row['actor'] for row in report['timeline']}), 1)
        self.assertTrue(report['timeline'][0]['actor'].startswith('flow-'))
        self.assertNotIn(flow, str([row['actor'] for row in report['timeline']]))
        self.assertIsNone(read_scope(token + 'tampered'))
        self.assertIsNone(read_scope(scope_token('account', True)))
        with patch('django.core.signing.time.time', return_value=1):
            expired = scope_token('flow', flow)
        self.assertIsNone(read_scope(expired))

    def test_empty_denominator_family_filter_and_bounded_timeline(self):
        get_user_model().objects.filter(pk=self.user.pk).update(date_joined=timezone.now() - timedelta(days=100))
        baseline = C.objects.filter(user=self.user).count()
        C.objects.bulk_create([C(user=self.user, event='scout_previewed') for _ in range(110)])
        E.objects.create(event='checkout_failed')
        report = build_product_analytics(family='consumer')
        self.assertIsNone(report['cohort'][0]['percent'])
        self.assertEqual(report['timeline_total'], 110 + baseline)
        self.assertEqual(len(report['timeline']), 100)
        self.assertTrue(all(row['family'] == 'consumer' for row in report['timeline']))

    def test_label_only_accepts_bounded_machine_labels(self):
        for value in ('https://private.test/token', 'person@example.test', '<script>', 'x' * 81, 'private brief'):
            self.assertEqual(label(value), 'Redacted label')
        self.assertEqual(label(''), 'Unattributed')
        self.assertEqual(label('employer_outreach_42'), 'employer_outreach_42')

    def test_daily_graph_preserves_totals_and_zero_days(self):
        self.event('scout_previewed')
        E.objects.create(event='feature_page_requested')
        report = build_product_analytics(days=7)
        self.assertEqual(len(report['daily']), 8)  # rolling window has two partial days
        self.assertEqual(sum(d['consumer'] + d['employer'] for d in report['daily']),
                         report['summary'][0]['count'])
        self.assertTrue(any(d['consumer'] == d['employer'] == 0 for d in report['daily']))

    def test_journeys_group_accounts_across_families_but_not_anonymous_flows(self):
        flow = 'c' * 32
        self.event('scout_previewed', user=None, alert=None, metadata={'scout_flow_id': flow})
        self.event('scout_created', metadata={'scout_flow_id': flow})
        E.objects.create(event='checkout_started', user=self.user)
        self.event('scout_previewed', user=None, alert=None)
        report = build_product_analytics(scope={'kind': 'account', 'value': self.user.pk})
        self.assertEqual(len(report['journeys']), 2)
        self.assertEqual(report['unlinked_sample'], 1)
        account = next(j for j in report['journeys'] if j['kind'] == 'Account')
        self.assertEqual(account['stage'], 'Checkout')
        self.assertTrue(account['selected'])
        self.assertEqual({r['family'] for r in report['timeline']}, {'consumer', 'employer'})
        self.assertTrue(all(r['actor'] != 'Anonymous / unlinked' for r in report['timeline']))
        self.assertEqual([r['at'] for r in report['chronological']],
                         sorted(r['at'] for r in report['timeline']))
        self.assertFalse(next(s for s in report['steps'] if s['label'] == 'Explore')['seen'])

    def test_journey_sample_and_list_are_bounded(self):
        C.objects.bulk_create([C(event='scout_previewed', metadata={'scout_flow_id': f'{i:032x}'})
                               for i in range(1005)])
        report = build_product_analytics()
        self.assertEqual(report['journey_sample'], 1000)
        self.assertEqual(len(report['journeys']), 50)
        self.assertTrue(report['journeys_truncated'])

    def test_employer_journey_has_employer_stages(self):
        C.objects.filter(user=self.user).delete()
        E.objects.create(user=self.user, event='job_draft_created')
        report = build_product_analytics(scope={'kind': 'account', 'value': self.user.pk})
        self.assertEqual([s['label'] for s in report['steps']],
                         ['Request*', 'Draft', 'Checkout', 'Complete', 'Review'])
        self.assertEqual(report['journeys'][0]['stage'], 'Draft')


class DiagnosticsAccessTests(TestCase):
    def setUp(self):
        self.url = reverse('admin_diagnostics')
        self.staff = get_user_model().objects.create_user('staff', is_staff=True)

    def grant(self):
        self.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='jobs', codename__in=('view_consumerfunnelevent', 'view_employerfunnelevent')))
        self.client.force_login(self.staff)

    def test_login_and_both_model_permissions_are_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.grant()
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_private_read_only_render_and_invalid_scope(self):
        self.grant()
        response = self.client.get(self.url + '?days=invalid&family=invalid')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Account-cohort activation')
        self.assertContains(response, 'No replay')
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.assertEqual(self.client.get(self.url + '?scope=invalid').status_code, 400)

    def test_raw_metadata_email_and_brief_are_not_rendered(self):
        self.grant()
        user = get_user_model().objects.create_user('customer', email='private@example.test')
        C.objects.create(user=user, event='checkout_failed', metadata={
            'email': user.email, 'error': 'PRIVATE EXCEPTION', 'brief': 'PRIVATE BRIEF',
        })
        response = self.client.get(self.url + '?view=journeys')
        for text in (user.email, 'PRIVATE EXCEPTION', 'PRIVATE BRIEF'):
            self.assertNotContains(response, text)
        self.assertContains(response, 'account-')

    def test_separate_views_and_failures_are_not_all_events(self):
        self.grant()
        user = get_user_model().objects.create_user('customer')
        C.objects.create(user=user, event='checkout_failed')
        C.objects.create(user=user, event='scout_previewed')
        for section in ('overview', 'journeys', 'campaigns', 'errors'):
            response = self.client.get(self.url, {'view': section})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['section'], section)
        response = self.client.get(self.url, {'view': 'errors'})
        self.assertEqual(response.context['a']['timeline_total'], 1)
        self.assertNotContains(response, 'Scout previewed')

    def test_account_detail_links_to_related_flow_not_itself(self):
        self.grant()
        user = get_user_model().objects.create_user('customer')
        C.objects.create(user=user, event='scout_created', metadata={'scout_flow_id': 'd' * 32})
        response = self.client.get(self.url, {'scope': scope_token('account', user.pk)})
        self.assertContains(response, 'Open related Scout flow')
        self.assertNotContains(response, 'Open linked account journey')
        flow_response = self.client.get(self.url, {'scope': scope_token('flow', 'd' * 32)})
        self.assertContains(flow_response, 'Open linked account journey')

    def test_username_links_require_user_admin_permission(self):
        self.grant()
        user = get_user_model().objects.create_user('customer-record')
        params = {'view': 'journeys', 'scope': scope_token('account', user.pk)}
        hidden = self.client.get(self.url, params)
        self.assertNotContains(hidden, 'customer-record')
        self.staff.user_permissions.add(Permission.objects.get(content_type__app_label='auth', codename='view_user'))
        shown = self.client.get(self.url, params)
        self.assertContains(shown, 'customer-record')
        self.assertContains(shown, reverse('admin:auth_user_change', args=[user.pk]))
        self.assertContains(shown, 'Open user record')

    def test_sample_cap_is_not_described_as_fifty_journeys_when_only_one_exists(self):
        self.grant()
        user = get_user_model().objects.create_user('customer')
        C.objects.bulk_create([C(user=user, event='scout_previewed') for _ in range(1001)])
        response = self.client.get(self.url, {'view': 'journeys'})
        self.assertContains(response, 'Recent-event sample capped')
        self.assertNotContains(response, 'Showing the 50 most recent journeys')
