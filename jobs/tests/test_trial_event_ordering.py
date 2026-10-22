from django.contrib.auth import get_user_model
from django.test import TestCase

from jobs.models import ConsumerFunnelEvent
from jobs.services.funnel import record_consumer_event


class TrialEventOrderingTests(TestCase):
    def test_later_trial_evidence_enriches_one_existing_start_and_is_not_erased(self):
        user = get_user_model().objects.create_user(username='trial-order')
        options = dict(user=user, dedupe_key='pro-payment:sub_order')
        first = record_consumer_event('payment_completed', metadata={'plan': 'monthly', 'source': 'partner'}, **options)
        second = record_consumer_event('payment_completed', metadata={'trial': True}, **options)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.metadata, {'plan': 'monthly', 'source': 'partner', 'trial': True})
        record_consumer_event('payment_completed', metadata={'trial': False}, **options)
        first.refresh_from_db()
        self.assertIs(first.metadata['trial'], True)
        self.assertEqual(ConsumerFunnelEvent.objects.filter(dedupe_key=options['dedupe_key']).count(), 1)

    def test_conflicting_account_cannot_enrich_another_accounts_event(self):
        user = get_user_model().objects.create_user(username='event-owner')
        other = get_user_model().objects.create_user(username='different-owner')
        first = record_consumer_event('payment_completed', user=user, dedupe_key='shared-key')
        record_consumer_event('payment_completed', user=other, dedupe_key='shared-key', metadata={'trial': True})
        first.refresh_from_db()
        self.assertNotIn('trial', first.metadata)
