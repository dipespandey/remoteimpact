from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, RequestFactory
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from growth.acquisition import claim_run, run_cycle, sync_partner, campaign_url, metrics_for, record_landing
from growth.models import AcquisitionAutomation, AcquisitionCampaign, AcquisitionDaily, AcquisitionRun
from jobs.models import ConsumerFunnelEvent
from jobs.link_models import LinkProspect


class AcquisitionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser('growth-owner', 'growth@example.org', 'local-test-only')
        cls.person = get_user_model().objects.create_user('growth-seeker')

    def prospect(self, **kwargs):
        return LinkProspect.objects.create(name='Climate careers', url='https://example.org/careers',
            audience='Climate professionals', fit_reason='Relevant climate career resources',
            contact_source_url='https://example.org/contact', contact_url='https://example.org/contact', **kwargs)

    @patch('growth.acquisition.sample_jobs', return_value=[])
    def test_research_prepares_tagged_draft_and_preserves_human_edits(self, jobs):
        p = self.prospect(last_checked_at=timezone.now(), check_status='ok')
        c = sync_partner(p, inspect=False)
        self.assertEqual(c.status, 'review')
        self.assertIn('link_prospect_', campaign_url(c))
        self.assertIn('No current catalogue sample', c.draft)
        self.assertEqual(p.status, 'research')
        c.draft = 'Human reviewed text'
        c.save()
        self.assertEqual(sync_partner(p, inspect=False).draft, 'Human reviewed text')

    @patch('growth.acquisition.link_prospecting.inspect_page')
    @patch('growth.acquisition.sample_jobs')
    def test_reddit_and_optouts_never_fetch_or_prepare(self, jobs, inspect):
        p = self.prospect(status='do_not_contact')
        self.assertEqual(sync_partner(p).status, 'hold')
        p.status, p.url = 'research', 'https://www.reddit.com/r/jobbit/'
        p.save()
        c = AcquisitionCampaign.objects.get(prospect=p)
        c.status = 'research'; c.save()
        self.assertEqual(sync_partner(p).status, 'hold')
        inspect.assert_not_called(); jobs.assert_not_called()

    def test_daily_lock_and_pause_and_requested_run(self):
        run, _ = claim_run()
        self.assertIsNone(claim_run(force=True))
        AcquisitionAutomation.objects.filter(pk=1).update(lease_until=None)
        self.assertIsNone(claim_run())
        AcquisitionAutomation.objects.filter(pk=1).update(requested=True)
        self.assertIsNotNone(claim_run())
        AcquisitionAutomation.objects.filter(pk=1).update(enabled=False, lease_until=None)
        self.assertIsNone(claim_run(force=True))

    @patch('growth.acquisition.sync_employers', return_value=0)
    @patch('growth.acquisition.discover', side_effect=RuntimeError('sensitive provider payload'))
    def test_failed_discovery_does_not_fake_success_or_leak_payload(self, discover, employers):
        run = run_cycle()
        self.assertEqual(run.status, 'partial')
        self.assertNotIn('sensitive', str(run.errors))
        self.assertIsNone(AcquisitionAutomation.objects.get(pk=1).lease_until)

    @patch('growth.acquisition.sample_jobs', return_value=[])
    def test_tagged_trial_is_not_cash_staff_excluded_unknown_stays_unknown(self, jobs):
        c = sync_partner(self.prospect(), inspect=False)
        for user in [self.person, self.owner]:
            ConsumerFunnelEvent.objects.create(user=user, event='account_created', metadata={'utm_source': c.source})
            ConsumerFunnelEvent.objects.create(user=user, event='scout_created', metadata={'utm_source': c.source})
            ConsumerFunnelEvent.objects.create(user=user, event='payment_completed', metadata={'utm_source': c.source, 'trial': True})
        metrics = metrics_for(c)
        self.assertEqual(metrics['accounts'], 1)
        self.assertEqual(metrics['scouts'], 1)
        self.assertEqual(metrics['verified_cash'], [])
        self.assertIsNone(metrics['landing_sessions'])

    def test_landing_session_day_dedupe_and_bot_and_error_exclusion(self):
        c = AcquisitionCampaign.objects.create(key='x', title='Partner', source='link_prospect_123', audience='seeker')
        request = RequestFactory().get('/jobs/?utm_source='+c.source, HTTP_USER_AGENT='Mozilla/5.0 test')
        request.user, request.session = self.person, {}
        record_landing(request, HttpResponse(status=404))
        self.assertFalse(AcquisitionDaily.objects.exists())
        record_landing(request, HttpResponse())
        record_landing(request, HttpResponse())
        self.assertEqual(AcquisitionDaily.objects.get().landing_sessions, 1)
        request.session = {}; request.META['HTTP_USER_AGENT'] = 'Mozilla Googlebot'
        record_landing(request, HttpResponse())
        self.assertEqual(AcquisitionDaily.objects.get().landing_sessions, 1)

    def test_returns_use_one_query_and_respect_cohort_window(self):
        c = AcquisitionCampaign.objects.create(key='returns', title='Returns', source='link_prospect_456', audience='seeker')
        now = timezone.now()
        signup = ConsumerFunnelEvent.objects.create(user=self.person, event='account_created', metadata={'utm_source': c.source})
        ConsumerFunnelEvent.objects.filter(pk=signup.pk).update(created_at=now-timedelta(days=10))
        event = ConsumerFunnelEvent.objects.create(user=self.person, event='scout_created')
        ConsumerFunnelEvent.objects.filter(pk=event.pk).update(created_at=now-timedelta(days=8))
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as captured:
            metrics = metrics_for(c)
        self.assertEqual(metrics['returning_accounts'], 1)
        self.assertEqual(metrics['mature_accounts'], 1)
        self.assertLessEqual(len(captured), 8)
        ConsumerFunnelEvent.objects.filter(pk=event.pk).update(created_at=now-timedelta(days=1))
        self.assertEqual(metrics_for(c)['returning_accounts'], 0)

    @patch('growth.acquisition.sample_jobs', return_value=[])
    def test_workspace_permissions_escaping_controls_and_details(self, jobs):
        c = sync_partner(self.prospect(), inspect=False)
        c.title = '<script>alert(1)</script>'; c.save()
        reader = get_user_model().objects.create_user('limited-growth-reader', is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename='view_acquisitioncampaign'))
        self.client.force_login(reader)
        self.assertEqual(self.client.get(reverse('admin_growth')).status_code, 403)
        self.client.force_login(self.owner)
        r = self.client.get(reverse('admin_growth'))
        self.assertContains(r, 'Next three opportunities')
        self.assertContains(r, '&lt;script&gt;')
        r = self.client.get(reverse('admin_growth_detail', args=[c.pk]))
        self.assertContains(r, 'Draft text')
        self.assertEqual(self.client.get(reverse('admin_growth_control')).status_code, 405)
        self.client.post(reverse('admin_growth_control'), {'action':'pause'})
        self.assertFalse(AcquisitionAutomation.objects.get(pk=1).enabled)
        self.client.post(reverse('admin_growth_update', args=[c.pk]), {'action':'hold', 'reason':'Needs review'})
        c.refresh_from_db(); self.assertEqual(c.status, 'hold')

    @patch('growth.acquisition.sync_employers', return_value=0)
    @patch('growth.acquisition.discover', return_value=2)
    @patch('growth.acquisition.link_prospecting.inspect_page', return_value='ok')
    @patch('growth.acquisition.sample_jobs', return_value=[])
    def test_cycle_connects_existing_prospect_to_measured_campaign(self, jobs, inspect, discover, employers):
        p = self.prospect()
        run = run_cycle()
        self.assertEqual(run.status, 'complete')
        c = AcquisitionCampaign.objects.get(prospect=p)
        self.assertIsNotNone(c.metrics_at)
        self.assertEqual(run.summary['campaigns_measured'], 1)
        self.assertIsNone(run_cycle())

    @patch('growth.acquisition.sample_jobs', return_value=[])
    def test_stale_worker_cannot_overwrite_hold_or_human_draft(self, jobs):
        from growth.acquisition import persist_proposal
        c = sync_partner(self.prospect(), inspect=False)
        AcquisitionCampaign.objects.filter(pk=c.pk).update(draft='New reviewed draft')
        c.draft = 'Old worker proposal'
        self.assertEqual(persist_proposal(c).draft, 'New reviewed draft')
        AcquisitionCampaign.objects.filter(pk=c.pk).update(status='hold')
        self.assertEqual(persist_proposal(c).status, 'hold')

    def test_expired_lease_records_interruption_before_recovery(self):
        run, _ = claim_run()
        AcquisitionAutomation.objects.filter(pk=1).update(lease_until=timezone.now()-timedelta(seconds=1), requested=True)
        self.assertIsNotNone(claim_run())
        run.refresh_from_db()
        self.assertEqual(run.status, 'interrupted')

    def test_cash_uses_exact_live_processor_reference_and_keeps_refunds(self):
        from growth.models import RevenueRun, CashMovement
        from jobs.models import Organization, Job, EmployerFunnelEvent
        org = Organization.objects.create(name='Example employer', slug='example-employer')
        job = Job.objects.create(title='Example role', slug='example-role', organization=org, stripe_payment_intent='pi_exact')
        c = AcquisitionCampaign.objects.create(key='cash', title='Cash test', source='growth_employer_999', audience='employer', organization=org)
        EmployerFunnelEvent.objects.create(user=self.person, job=job, event='payment_completed', metadata={'campaign_labels':{'utm_source':c.source}})
        now = timezone.now()
        run = RevenueRun.objects.create(account_id='acct_synthetic', livemode=True, since=now-timedelta(days=30), until=now, complete=True)
        base = dict(account_id='acct_synthetic', payment_intent_id='pi_exact', scope_verified=True, currency='usd', occurred_at=now, available_at=now, last_run=run)
        CashMovement.objects.create(**base, external_id='txn_charge', kind='charge', amount_minor=9900, fee_minor=300, net_minor=9600, livemode=True)
        CashMovement.objects.create(**base, external_id='txn_refund', kind='refund', amount_minor=-1000, fee_minor=0, net_minor=-1000, livemode=True)
        CashMovement.objects.create(**base, external_id='txn_test', kind='charge', amount_minor=10000, fee_minor=0, net_minor=10000, livemode=False)
        self.assertEqual(metrics_for(c)['verified_cash'], [{'currency':'usd','net_minor':8600,'gross_minor':8900}])
