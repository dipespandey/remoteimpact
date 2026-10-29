import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.signing import TimestampSigner
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from jobs.tests.test_scout_results import setup_scout_fixture
from jobs.models import ConsumerFunnelEvent as E, JobAlert, NewsletterSubscriber, ScoutContact
from jobs.services.job_scout_service import JobScoutService
from jobs.services import scout_funnel
from allauth.account.signals import user_signed_up


@override_settings(SECURE_SSL_REDIRECT=False,ACCOUNT_RATE_LIMITS={})
class ScoutFunnelTests(TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch('jobs.services.email_service.email_service.send_email',return_value=True).start()
        setup_scout_fixture(self)
        self.user=get_user_model().objects.create_user('flow-fixture','fixture@example.org','test-fixture-password-531!')
        self.client.force_login(self.user)
    def preview(self, client=None, **extra):
        client=client or self.client
        with patch.object(JobScoutService,'search_intent',return_value=(self.prefs,[self.job])):
            return client.get(reverse('jobs:job_scout_preview'),{'intent':self.intent,'source':'home_hero_example_python',**extra})

    def test_anonymous_signup_and_create_share_flow_across_session_rotation(self):
        self.client.logout()
        page=self.preview();flow=page.context['scout_flow_id'];next_path=page.context['next_path']
        old_session=self.client.session.session_key
        self.client.get(reverse('jobs:job_scout_signup_start'),{'next':next_path,'source':'home_hero_example_python'})
        page=self.client.post(reverse('account_signup'),{'email':'flow@example.org','password1':'test-flow-password-531!','password2':'test-flow-password-531!','next':next_path},follow=True)
        self.assertNotEqual(old_session,self.client.session.session_key)
        self.assertEqual(page.context['scout_flow_id'],flow)
        account=E.objects.get(event=E.Event.ACCOUNT_CREATED,user=page.wsgi_request.user)
        self.assertEqual(account.metadata['scout_flow_id'],flow)
        with patch.object(JobScoutService,'parse_intent',return_value=self.prefs),patch.object(JobScoutService,'get_matches',return_value=[self.job]),patch('jobs.views.job_alerts.email_service.send_job_alert',return_value=True):
            self.client.post(reverse('jobs:job_alert_create'),{'intent_text':self.intent,'frequency':'once','source':'home_hero_example_python','scout_flow':flow})
        created=E.objects.get(event=E.Event.SCOUT_CREATED)
        self.assertEqual(created.metadata['scout_flow_id'],flow)
        self.assertEqual(E.objects.get(event=E.Event.INITIAL_EMAIL_SENT).metadata['scout_flow_id'],flow)
        self.assertEqual(E.objects.get(event=E.Event.SCOUT_SUBMIT_RESULT).metadata['outcome'],'created')

    @override_settings(SCOUT_PREVIEW_ASYNC_ENABLED=True)
    def test_async_preview_signup_and_create_keep_same_flow(self):
        from jobs.models import ScoutPreviewTask
        task = SimpleNamespace(pk="00000000-0000-0000-0000-000000000001",
            state=ScoutPreviewTask.State.SUCCEEDED, State=ScoutPreviewTask.State,
            result={"preferences": self.prefs, "diagnostics": {}})
        with patch("jobs.services.scout_tasks.enqueue_preview", return_value=(task, False)), \
             patch("jobs.services.scout_tasks.hydrate_result", return_value=([self.job], [], False)):
            self.test_anonymous_signup_and_create_share_flow_across_session_rotation()

    def test_anonymous_contact_capture_is_purpose_limited_and_verified(self):
        self.client.logout()
        page = self.preview()
        flow = page.context["scout_flow_id"]
        self.assertContains(page, "Want us to follow up?")
        self.assertContains(page, 'name="marketing_opt_in"')

        invalid = self.client.post(
            reverse("jobs:scout_contact_capture"),
            {"scout_flow": flow, "email": "reader@example.org"},
        )
        self.assertEqual(invalid.status_code, 302)
        self.assertTrue(invalid.url.startswith(reverse("jobs:job_scout_preview")))
        self.assertFalse(ScoutContact.objects.exists())

        captured = self.client.post(
            reverse("jobs:scout_contact_capture"),
            {
                "scout_flow": flow,
                "email": "reader@example.org",
                "contact_consent": "on",
                "marketing_opt_in": "on",
            },
        )
        self.assertEqual(captured.status_code, 302)
        self.assertTrue(captured.url.startswith(reverse("jobs:job_scout_preview")))
        contact = ScoutContact.objects.get(email="reader@example.org", scout_flow_id=flow)
        self.assertIsNone(contact.verified_at)
        self.assertTrue(contact.contact_consent)
        self.assertTrue(contact.marketing_opt_in)
        event = E.objects.get(event=E.Event.SCOUT_CONTACT_CAPTURED)
        self.assertNotIn("reader@example.org", json.dumps(event.metadata))

        token = TimestampSigner(salt="scout-contact-verify").sign(str(contact.pk))
        verified = self.client.get(reverse("jobs:scout_contact_verify", kwargs={"token": token}))
        self.assertRedirects(verified, reverse("jobs:home"), fetch_redirect_response=False)
        contact.refresh_from_db()
        self.assertIsNotNone(contact.verified_at)
        subscriber = NewsletterSubscriber.objects.get(email="reader@example.org")
        self.assertTrue(subscriber.confirmed)
        self.assertTrue(E.objects.filter(event=E.Event.SCOUT_CONTACT_VERIFIED).exists())

    @patch('jobs.views.job_alerts.email_service.send_scout_contact_verification', return_value=True)
    def test_popup_contact_stays_out_of_url_and_links_to_preview(self, send_verification):
        self.client.logout()
        target = reverse('jobs:job_scout_preview')
        invalid = self.client.post(target, {'intent': self.intent, 'source': 'pricing_free', 'email': 'reader@example.org'})
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(ScoutContact.objects.exists())
        invalid = self.client.post(target, {'intent': self.intent, 'source': 'pricing_free', 'email': 'invalid', 'contact_consent': 'on'})
        self.assertEqual(invalid.status_code, 400)
        send_verification.assert_not_called()
        for source in ('pricing_free', 'home_hero_launcher'):
            previous_count = ScoutContact.objects.count()
            response = self.client.post(target, {'intent': self.intent, 'source': source})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(ScoutContact.objects.count(), previous_count)
            captured = self.client.post(target, {'intent': self.intent, 'source': source, 'email': 'reader@example.org', 'contact_consent': 'on'})
            self.assertEqual(captured.status_code, 302)
            self.assertNotIn('reader', captured.url)
            self.assertNotIn('email=', captured.url)
            with patch.object(JobScoutService, 'search_intent', return_value=(self.prefs, [self.job])):
                preview = self.client.get(captured.url)
            contact = ScoutContact.objects.get(scout_flow_id=preview.context['scout_flow_id'])
            self.assertEqual(contact.email, 'reader@example.org')
            self.assertTrue(contact.contact_consent)
            self.assertFalse(contact.marketing_opt_in)
            self.assertIsNone(contact.verified_at)
        self.assertEqual(send_verification.call_count, 2)

    def test_sample_personalization_keeps_entry_source_and_flow(self):
        page=self.preview();flow=page.context['scout_flow_id']
        self.assertContains(page,'Personalize this search')
        self.assertContains(page,'Use this exact search')
        self.assertContains(page,'id="personalized-intent"')
        changed='Remote Python engineering roles in Canada, no internships.'
        page=self.preview(intent=changed,source='scout_personalized',scout_flow=flow)
        self.assertEqual(page.context['scout_flow_id'],flow)
        self.assertFalse(page.context['sample_preview'])
        event=E.objects.get(event=E.Event.SCOUT_PERSONALIZED)
        self.assertEqual(event.metadata['scout_source'],'home_hero_example_python')
        self.assertTrue(event.metadata['brief_changed'])
        self.assertNotIn('intent_text',event.metadata)

    def test_flow_cannot_be_replayed_from_another_session(self):
        page=self.preview();flow=page.context['scout_flow_id']
        other=Client()
        other_page=self.preview(other,scout_flow=flow)
        self.assertNotEqual(other_page.context['scout_flow_id'],flow)

    def test_refresh_deduplicates_preview_and_form_view(self):
        first=self.preview();self.preview()
        self.assertEqual(E.objects.filter(event=E.Event.SCOUT_PREVIEWED).count(),1)
        self.client.get(first.context['next_path']);self.client.get(first.context['next_path'])
        self.assertEqual(E.objects.filter(event=E.Event.SCOUT_FORM_VIEWED).count(),1)

    def test_invalid_submit_retains_flow_and_tracks_only_field_names(self):
        flow=self.preview().context['scout_flow_id']
        response=self.client.post(reverse('jobs:job_alert_create'),{'intent_text':'short','frequency':'once','source':'test_source','scout_flow':flow})
        self.assertContains(response,f'name="scout_flow" value="{flow}"')
        self.assertContains(response,'name="source" value="test_source"')
        result=E.objects.get(event=E.Event.SCOUT_SUBMIT_RESULT)
        self.assertEqual(result.metadata['outcome'],'invalid')
        self.assertIn('intent_text',result.metadata['invalid_fields'])
        self.assertNotIn('short',json.dumps(result.metadata))
        self.assertFalse(JobAlert.objects.exists())

    def test_generic_signup_does_not_inherit_pending_scout(self):
        self.client.logout();page=self.preview()
        self.client.get(reverse('jobs:job_scout_signup_start'),{'next':page.context['next_path']})
        self.client.post(reverse('account_signup'),{'email':'generic@example.org','password1':'test-generic-password-531!','password2':'test-generic-password-531!'},follow=True)
        account=E.objects.filter(event=E.Event.ACCOUNT_CREATED).exclude(user=self.user).get()
        self.assertNotIn('scout_flow_id',account.metadata)

    def test_social_signup_uses_allauth_state_next(self):
        self.client.logout();page=self.preview()
        self.client.get(reverse('jobs:job_scout_signup_start'),{'next':page.context['next_path']})
        user=get_user_model().objects.create_user('social-flow','social-flow@example.org','test-social-password-531!')
        request=page.wsgi_request;request.session=self.client.session
        user_signed_up.send(sender=user.__class__,request=request,user=user,sociallogin=SimpleNamespace(state={'next':page.context['next_path']}))
        event=E.objects.get(event=E.Event.ACCOUNT_CREATED,user=user)
        self.assertEqual(event.metadata['scout_flow_id'],page.context['scout_flow_id'])

    def test_report_excludes_unlinked_and_generic_accounts(self):
        page=self.preview();flow=page.context['scout_flow_id']
        E.objects.create(event=E.Event.SCOUT_PREVIEWED,metadata={'source':'legacy'})
        E.objects.create(event=E.Event.SCOUT_SUBMIT_STARTED,metadata={'scout_flow_id':flow})
        for outcome in ['invalid','created']:
            E.objects.create(event=E.Event.SCOUT_SUBMIT_RESULT,metadata={'scout_flow_id':flow,'outcome':outcome})
        stream=StringIO();call_command('scout_funnel_report',days=1,json=True,stdout=stream)
        report=json.loads(stream.getvalue());self.assertEqual(report['totals']['flows'],1)
        self.assertEqual(report['unlinked_legacy_preview_events'],1)
        self.assertEqual(report['totals']['stage_flows']['account_created'],0)
        self.assertEqual(report['totals']['submit_attempt_outcomes'],{'invalid':1,'created':1})
        self.assertNotIn(flow,stream.getvalue())

    def test_expired_flow_is_replaced(self):
        page=self.preview();flow=page.context['scout_flow_id']
        session=self.client.session;flows=session[scout_funnel.KEY];flows[flow]['started']-=86401;session[scout_funnel.KEY]=flows;session.save()
        self.assertNotEqual(self.preview(scout_flow=flow).context['scout_flow_id'],flow)

    def test_blocked_and_duplicate_attempts_do_not_resend(self):
        flow=self.preview().context['scout_flow_id']
        payload={'intent_text':self.intent,'frequency':'once','scout_flow':flow}
        with patch.object(JobScoutService,'parse_intent',return_value=self.prefs),patch.object(JobScoutService,'get_matches',return_value=[self.job]),patch('jobs.views.job_alerts.email_service.send_job_alert',return_value=True) as mail:
            self.client.post(reverse('jobs:job_alert_create'),payload)
            self.client.post(reverse('jobs:job_alert_create'),payload)
            payload['intent_text']='Different remote engineering roles in Canada, no internships.'
            self.client.post(reverse('jobs:job_alert_create'),payload)
        self.assertEqual(mail.call_count,1)
        outcomes=list(E.objects.filter(event=E.Event.SCOUT_SUBMIT_RESULT).values_list('metadata__outcome',flat=True))
        self.assertCountEqual(outcomes,['created','duplicate','blocked'])

    def test_matching_exception_records_error_without_exception_text(self):
        flow=self.preview().context['scout_flow_id']
        with patch.object(JobScoutService,'parse_intent',side_effect=RuntimeError('private data should never be copied')):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse('jobs:job_alert_create'),{'intent_text':self.intent,'frequency':'once','scout_flow':flow})
        event=E.objects.get(event=E.Event.SCOUT_SUBMIT_RESULT)
        self.assertEqual(event.metadata['outcome'],'error')
        self.assertEqual(event.metadata['error_type'],'RuntimeError')
        self.assertNotIn('private data',json.dumps(event.metadata))

    def test_refresh_of_personalized_preview_is_not_another_personalization(self):
        flow=self.preview().context['scout_flow_id']
        params={'intent':'Remote Python engineering roles in Canada, no internships.','source':'scout_personalized','scout_flow':flow}
        self.preview(**params);self.preview(**params)
        self.assertEqual(E.objects.filter(event=E.Event.SCOUT_PERSONALIZED).count(),1)

    def test_switching_account_does_not_claim_previous_users_flow(self):
        flow=self.preview().context['scout_flow_id']
        other=get_user_model().objects.create_user('other-flow','other-flow@example.org','test-other-password-531!')
        self.client.force_login(other)
        self.assertNotEqual(self.preview(scout_flow=flow).context['scout_flow_id'],flow)

    def test_returning_flow_is_not_counted_as_a_new_window_cohort(self):
        from datetime import timedelta
        from django.utils import timezone
        flow=self.preview().context['scout_flow_id']
        first=E.objects.get(event=E.Event.SCOUT_PREVIEWED)
        E.objects.filter(pk=first.pk).update(created_at=timezone.now()-timedelta(days=2))
        E.objects.create(event=E.Event.SCOUT_PREVIEWED,metadata={'scout_flow_id':flow})
        stream=StringIO();call_command('scout_funnel_report',days=1,json=True,stdout=stream)
        self.assertEqual(json.loads(stream.getvalue())['totals']['flows'],0)

    def test_direct_create_form_is_tracked_separately_from_preview_cohort(self):
        page=self.client.get(reverse('jobs:job_alert_create'),{'intent':self.intent})
        self.assertTrue(page.context['scout_prefilled'])
        self.assertEqual(page.context['form'].initial['intent_text'],self.intent)
        self.assertTrue(page.context['scout_flow_id'])
        self.assertEqual(E.objects.filter(event=E.Event.SCOUT_FORM_VIEWED).count(),1)
        stream=StringIO();call_command('scout_funnel_report',days=1,json=True,stdout=stream)
        report=json.loads(stream.getvalue())
        self.assertEqual(report['form_flows_without_observed_preview'],1)
        self.assertEqual(report['totals']['flows'],0)
