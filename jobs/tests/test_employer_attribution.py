from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from jobs.models import Job, Organization, EmployerFunnelEvent
from jobs.services.payment_service import PaymentService
from jobs.services.employer_attribution import clean_labels, stripe_labels, request_labels, SNAPSHOT_KEY
from jobs.tests.test_employer_product import EmployerTestBase


class CheckoutAttributionTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name='Employer')
        self.job = Job.objects.create(title='Program lead', slug='program-lead-attribution', organization=org,
                                      description='Role', raw_data={'keep': 'source evidence'}, is_active=False)
        self.labels = {'utm_source': 'employer_outreach_42', 'utm_medium': 'email', 'utm_campaign': 'hiring'}

    def test_only_bounded_label_strings_are_retained(self):
        self.assertEqual(clean_labels({'utm_source':'  partner\n board ', 'utm_campaign':'a'*120,
                                       'utm_medium':42, 'email':'private@example.com', 'job_id':'99'}),
                         {'utm_source':'partner board','utm_campaign':'a'*100})
        self.assertEqual(request_labels(SimpleNamespace(session={'acquisition_attribution': []})), {})

    @patch('jobs.services.payment_service.stripe.checkout.Session.create')
    def test_posting_freezes_first_touch_for_identical_retries(self, create):
        create.return_value = SimpleNamespace(id='cs_posting', url='https://checkout.example/posting')
        PaymentService.create_checkout_session(self.job, 'https://example.org', attribution=self.labels)
        first = create.call_args.kwargs
        PaymentService.create_checkout_session(self.job, 'https://example.org', attribution={'utm_source':'later'})
        self.assertEqual(create.call_args.kwargs, first)
        self.assertEqual(first['metadata']['acquisition_utm_source'], 'employer_outreach_42')
        self.assertEqual(first['payment_intent_data']['metadata'], first['metadata'])
        self.assertEqual(first['line_items'][0]['price_data']['unit_amount'], 9900)
        self.job.refresh_from_db()
        self.assertEqual(self.job.raw_data['keep'], 'source evidence')
        self.assertEqual(self.job.raw_data[SNAPSHOT_KEY]['labels'], self.labels)

    @patch('jobs.services.payment_service.stripe.checkout.Session.create')
    def test_unknown_first_touch_stays_unknown_after_failed_api_call(self, create):
        create.side_effect = RuntimeError('ambiguous timeout')
        with self.assertRaises(RuntimeError):
            PaymentService.create_checkout_session(self.job, 'https://example.org', attribution={})
        first = create.call_args.kwargs
        create.side_effect = None
        create.return_value = SimpleNamespace(id='cs_retry', url='https://checkout.example/retry')
        PaymentService.create_checkout_session(self.job, 'https://example.org', attribution=self.labels)
        self.assertEqual(create.call_args.kwargs, first)
        self.assertNotIn('acquisition_utm_source', first['metadata'])

    @patch('jobs.services.payment_service.stripe.checkout.Session.create')
    def test_existing_legacy_checkout_is_not_relabelled(self, create):
        Job.objects.filter(pk=self.job.pk).update(stripe_checkout_session_id='cs_legacy')
        create.return_value = SimpleNamespace(id='cs_legacy', url='https://checkout.example/legacy')
        PaymentService.create_checkout_session(self.job, 'https://example.org', attribution=self.labels)
        self.assertEqual(create.call_args.kwargs['metadata'], {'job_id':str(self.job.id)})
        self.assertNotIn('payment_intent_data', create.call_args.kwargs)

    def test_posting_webhook_retains_checkout_labels_once(self):
        metadata = {'job_id':str(self.job.pk), **stripe_labels(self.labels)}
        event = {'type':'checkout.session.completed','data':{'object':{'id':'cs_paid','mode':'payment',
                 'payment_status':'paid','payment_intent':'pi_paid','metadata':metadata}}}
        PaymentService.handle_webhook_event(event)
        PaymentService.handle_webhook_event(event)
        rows = EmployerFunnelEvent.objects.filter(job=self.job,event='payment_completed')
        self.assertEqual(rows.count(),1)
        self.assertEqual(rows.get().metadata['campaign_labels'],self.labels)

    @patch('jobs.services.payment_service.stripe.checkout.Session.retrieve')
    def test_browser_return_retains_same_checkout_labels(self, retrieve):
        retrieve.return_value = SimpleNamespace(id='cs_browser',payment_status='paid',payment_intent='pi_browser',
                                                metadata={'job_id':str(self.job.pk), **stripe_labels(self.labels)})
        ok, _ = PaymentService.verify_payment('cs_browser',str(self.job.pk))
        self.assertTrue(ok)
        self.assertEqual(EmployerFunnelEvent.objects.get(job=self.job).metadata['campaign_labels'], self.labels)

    def test_feature_payment_keeps_its_own_session_campaign(self):
        first = {'id':'cs_feature1','amount_total':4900,'metadata':{'job_id':str(self.job.pk),'product':'feature',**stripe_labels(self.labels)}}
        PaymentService._activate_feature(first)
        PaymentService._activate_feature(first)
        PaymentService._activate_feature({'id':'cs_feature2','amount_total':4900,'metadata':{'job_id':str(self.job.pk),'product':'feature',**stripe_labels({'utm_source':'second'})}})
        rows = list(EmployerFunnelEvent.objects.filter(job=self.job).order_by('id'))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0].metadata['campaign_labels'],self.labels)
        self.assertEqual(rows[1].metadata['campaign_labels'],{'utm_source':'second'})


    @patch('jobs.services.payment_service.stripe.checkout.Session.create')
    def test_new_and_resumed_post_views_pass_session_campaign(self, create):
        from django.test import RequestFactory
        from jobs.views.jobs import PostJobView
        from jobs.views.payments import JobCheckoutView
        create.return_value = SimpleNamespace(id='cs_post', url='https://checkout.example/post')
        user = get_user_model().objects.create_user('employer-attribution', 'employer@example.com', 'pw')
        request = RequestFactory().post('/jobs/post/')
        request.user = user
        request.session = {'acquisition_attribution': self.labels}
        form = SimpleNamespace(cleaned_data={})
        for cls in [PostJobView, JobCheckoutView]:
            view = cls()
            view.request = request
            view.job = self.job
            with patch('jobs.services.job_service.JobService.create_job', return_value=self.job), patch('jobs.services.job_service.JobService.update_job', return_value=self.job):
                response = view.form_valid(form)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(create.call_args.kwargs['metadata']['acquisition_utm_source'], 'employer_outreach_42')

    def test_legacy_paid_session_does_not_invent_labels(self):
        PaymentService._mark_job_paid(self.job, session_id='cs_legacy', payment_intent='pi_legacy')
        self.assertEqual(EmployerFunnelEvent.objects.get(job=self.job).metadata, {})


class FeatureRequestAttributionTests(EmployerTestBase):
    @patch('jobs.services.payment_service.stripe.checkout.Session.create')
    def test_campaign_survives_get_to_post_without_query_parameters(self, create):
        create.return_value = SimpleNamespace(id='cs_feature',url='https://checkout.example/feature')
        url = reverse('jobs:feature_job',kwargs={'slug':self.job.slug})
        self.client.get(url+'?utm_source=link_prospect_18&utm_medium=referral&utm_campaign=editorial_partnerships')
        response = self.client.post(url, {'source':'employers'})
        self.assertEqual(response.status_code,302)
        metadata = create.call_args.kwargs['metadata']
        self.assertEqual(metadata['acquisition_utm_source'],'link_prospect_18')
        self.assertEqual(metadata['acquisition_utm_campaign'],'editorial_partnerships')
        self.assertEqual(metadata['source'],'employers')
