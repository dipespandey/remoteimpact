from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.contrib.auth.models import AnonymousUser

from jobs.models import EmployerFunnelEvent
from jobs.services.funnel import record_employer_page_request
from jobs.tests.test_employer_product import EmployerTestBase, BROWSER


class PageRequestTests(EmployerTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.url = reverse('jobs:feature_job', kwargs={'slug': self.job.slug}) + '?source=outreach'

    def test_head_is_not_a_view_and_repeated_gets_are_deduplicated(self):
        self.client.head(self.url, HTTP_USER_AGENT=BROWSER)
        self.assertFalse(EmployerFunnelEvent.objects.exists())
        self.client.get(self.url, HTTP_USER_AGENT=BROWSER)
        self.client.get(self.url, HTTP_USER_AGENT=BROWSER)
        event = EmployerFunnelEvent.objects.get()
        self.assertEqual(event.event, 'feature_page_requested')
        self.assertEqual(event.metadata['signal'], 'unverified_request')
        self.assertEqual(event.metadata['method'], 'GET')

    def test_email_protection_is_not_claimed_as_human_interest(self):
        self.client.get(self.url, HTTP_USER_AGENT=BROWSER,
                        HTTP_REFERER='https://link.edgepilot.com/private-token')
        event = EmployerFunnelEvent.objects.get()
        self.assertEqual(event.metadata['signal'], 'email_link_protection')
        self.assertNotIn('private-token', str(event.metadata))
        self.assertIsNone(event.user_id)

    def test_bots_and_staff_do_not_count(self):
        self.client.get(self.url, HTTP_USER_AGENT='Googlebot')
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user('staff', is_staff=True)
        self.client.force_login(user)
        self.client.get(self.url, HTTP_USER_AGENT=BROWSER)
        self.assertFalse(EmployerFunnelEvent.objects.exists())

    def test_cache_failure_does_not_inflate_events(self):
        with patch('django.core.cache.cache.add', side_effect=RuntimeError):
            self.client.get(self.url, HTTP_USER_AGENT=BROWSER)
        self.assertFalse(EmployerFunnelEvent.objects.exists())

    def test_posting_head_is_not_counted(self):
        request = RequestFactory().head('/jobs/post/', HTTP_USER_AGENT=BROWSER)
        request.user = AnonymousUser()
        record_employer_page_request(request, page='post')
        self.assertFalse(EmployerFunnelEvent.objects.exists())

    def test_historical_rows_are_relabelled_without_deletion(self):
        legacy = EmployerFunnelEvent.objects.create(event='post_job_viewed', job=self.job,
                                                    metadata={'page': 'feature', 'source': 'outreach'})
        post = EmployerFunnelEvent.objects.create(event='post_job_viewed', metadata={'page': 'post'})
        migration = import_module('jobs.migrations.0081_employer_page_request_labels')
        editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        migration.reclassify_feature_requests(apps, editor)
        legacy.refresh_from_db()
        post.refresh_from_db()
        self.assertEqual(EmployerFunnelEvent.objects.count(), 2)
        self.assertEqual(legacy.event, 'feature_page_requested')
        self.assertEqual(legacy.metadata['original_event'], 'post_job_viewed')
        self.assertEqual(post.event, 'post_job_viewed')
        migration.restore_legacy_labels(apps, editor)
        legacy.refresh_from_db()
        self.assertEqual(legacy.event, 'post_job_viewed')
        self.assertEqual(legacy.metadata, {'page': 'feature', 'source': 'outreach'})
