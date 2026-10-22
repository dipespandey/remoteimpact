from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from jobs.models import AssistantSubscription, ConsumerFunnelEvent, JobAlert, SeekerProfile


class ConsumerFunnelReportTests(TestCase):
    def report(self):
        output = StringIO()
        call_command('consumer_funnel_report', days=28, stdout=output)
        return output.getvalue()

    def test_cohort_counts_people_once_and_keeps_trial_events_separate(self):
        User = get_user_model()
        recent = User.objects.create_user(username='recent', email='recent@example.test')
        other = User.objects.create_user(username='other', email='other@example.test')
        old = User.objects.create_user(username='old', email='old@example.test')
        User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=60))
        SeekerProfile.objects.update_or_create(user=recent, defaults={'wizard_completed': True})
        SeekerProfile.objects.update_or_create(user=other, defaults={'wizard_completed': False})
        JobAlert.objects.create(user=recent)
        JobAlert.objects.create(user=recent)
        JobAlert.objects.create(user=old)
        for metadata in [{'trial': True}, {'trial': True}, {'plan': 'pass'}]:
            ConsumerFunnelEvent.objects.create(user=recent, event='payment_completed', metadata=metadata)
        ConsumerFunnelEvent.objects.create(user=old, event='payment_completed')
        text = self.report()
        self.assertIn('Accounts with a new Scout: 1 / 2 (50.00%)', text)
        self.assertIn('Seeker profiles completed: 1 / 2 (50.00%)', text)
        self.assertIn('Accounts with Pro start event (includes trials): 1 / 2 (50.00%)', text)
        self.assertRegex(text, r'Recorded trial starts\s+2')
        self.assertRegex(text, r'Non-trial completion events\s+2')
        self.assertNotIn('Payments completed', text)
        self.assertNotIn('Checkout -> paid', text)

    def test_empty_denominators_and_expired_entitlements(self):
        user = get_user_model().objects.create_user(username='expired', email='expired@example.test')
        get_user_model().objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(days=60))
        AssistantSubscription.objects.update_or_create(user=user, defaults={
            'is_subscribed': True, 'expires_at': timezone.now() - timedelta(days=1),
        })
        text = self.report()
        self.assertIn('Accounts with a new Scout: 0 / 0 (n/a)', text)
        self.assertIn('Seeker profiles completed: 0 / 0 (n/a)', text)
        self.assertRegex(text, r'Active Pro entitlements \(all accounts\)\s+0')
