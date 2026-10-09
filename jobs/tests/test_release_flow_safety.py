from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from django.db import connection
from jobs.services.importers.common import _reset_idle_connections

class ReleaseFlowSafetyTests(TestCase):
    def test_career_advising_service_is_not_a_job_opening(self):
        from jobs.services.importers.common import _is_generic_listing_title
        self.assertTrue(_is_generic_listing_title("Get Career Advising"))
        self.assertFalse(_is_generic_listing_title("Career Advisor"))

    def test_import_cleanup_preserves_active_transaction(self):
        self.assertTrue(connection.in_atomic_block)
        with patch.object(connection, 'close_if_unusable_or_obsolete') as cleanup:
            _reset_idle_connections()
        cleanup.assert_not_called()
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            self.assertEqual(cursor.fetchone(), (1,))

    @override_settings(STRIPE_WEBHOOK_SECRET='synthetic-webhook-secret')
    @patch('jobs.views.payments.PaymentService.handle_webhook_event', side_effect=RuntimeError('temporary failure'))
    @patch('jobs.views.payments.stripe.Webhook.construct_event', return_value={'id':'evt_synthetic'})
    def test_webhook_handler_failure_remains_retryable(self, verified, handler):
        response=self.client.post(reverse('jobs:stripe_webhook'),data='{}',content_type='application/json',HTTP_STRIPE_SIGNATURE='synthetic')
        self.assertEqual(response.status_code,503)
        handler.assert_called_once()

    @override_settings(STRIPE_WEBHOOK_SECRET='synthetic-webhook-secret')
    @patch('jobs.views.payments.PaymentService.handle_webhook_event')
    @patch('jobs.views.payments.stripe.Webhook.construct_event', return_value={'id':'evt_synthetic'})
    def test_successful_webhook_acknowledged(self, verified, handler):
        response=self.client.post(reverse('jobs:stripe_webhook'),data='{}',content_type='application/json',HTTP_STRIPE_SIGNATURE='synthetic')
        self.assertEqual(response.status_code,200)
