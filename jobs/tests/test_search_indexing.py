from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from jobs.models import Job, Organization
from jobs.services.search_indexing import acknowledge, candidates, fingerprint, pending


@override_settings(INDEXNOW_ENABLED=True, SITE_URL='https://remoteimpact.org', INDEXNOW_KEY='test-public-key')
class SearchIndexingTests(TestCase):
    def setUp(self):
        for mock in [patch('jobs.services.embedding_service.embed_job', return_value=[0.] * 384),
                     patch('jobs.signals.threading.Thread')]:
            mock.start()
            self.addCleanup(mock.stop)
        self.org = Organization.objects.create(name='Public employer', slug='public-employer')

    def job(self, **kwargs):
        defaults = {'title': 'Software Engineer', 'slug': f'index-role-{Job.objects.count()}',
            'organization': self.org, 'source': 'ashby', 'description': 'Build useful software systems.',
            'location': 'Remote (US)', 'country': 'United States',
            'application_url': 'https://example.org/software-role'}
        defaults.update(kwargs)
        return Job.objects.create(**defaults)

    def test_dry_run_and_disabled_modes_do_not_transmit(self):
        job = self.job()
        with patch('jobs.management.commands.indexnow_submit.requests.post') as post:
            call_command('indexnow_submit', stdout=StringIO())
            with override_settings(INDEXNOW_ENABLED=False):
                call_command('indexnow_submit', send=True, stdout=StringIO())
            post.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.indexing_state, {})

    def test_acknowledged_batch_survives_repeated_runs_and_feed_sightings(self):
        job = self.job()
        before = job.updated_at
        with patch('jobs.management.commands.indexnow_submit.requests.post', return_value=Mock(status_code=200)) as post:
            call_command('indexnow_submit', send=True, stdout=StringIO())
            job.refresh_from_db()
            self.assertEqual(job.updated_at, before)
            self.assertEqual(job.indexing_state['indexnow']['type'], 'URL_UPDATED')
            Job.objects.filter(pk=job.pk).update(last_seen_at=timezone.now(), updated_at=timezone.now())
            call_command('indexnow_submit', send=True, stdout=StringIO())
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.kwargs['json']['host'], 'remoteimpact.org')
            self.assertEqual(post.call_args.kwargs['json']['key'], 'test-public-key')
        job.refresh_from_db()
        self.assertFalse(pending(job, 'indexnow'))
        Job.objects.filter(pk=job.pk).update(requirements='Experience building public APIs.')
        job.refresh_from_db()
        self.assertTrue(pending(job, 'indexnow'))

    def test_rejected_batch_stays_pending_and_does_not_loop(self):
        job = self.job()
        with patch('jobs.management.commands.indexnow_submit.requests.post', return_value=Mock(status_code=429)) as post:
            with self.assertRaises(CommandError):
                call_command('indexnow_submit', send=True, stdout=StringIO())
            self.assertEqual(post.call_count, 1)
        job.refresh_from_db()
        self.assertEqual(job.indexing_state, {})
        self.assertTrue(pending(job, 'indexnow'))

    def test_title_rendering_policy_change_notifies_without_refreshing_job_dates(self):
        job = self.job()
        before = job.updated_at
        acknowledge(job, 'indexnow', fingerprint(job))
        job.refresh_from_db()
        with patch('jobs.services.seo_text.job_title_tag', return_value='A clarified public role title'):
            self.assertTrue(pending(job, 'indexnow'))
        job.refresh_from_db()
        self.assertEqual(job.updated_at, before)

    def test_closure_without_save_and_reopening_each_notify_once(self):
        job = self.job()
        acknowledge(job, 'indexnow', fingerprint(job))
        Job.objects.filter(pk=job.pk).update(expires_at=timezone.now()-timedelta(days=3),
            updated_at=timezone.now()-timedelta(days=10), posted_at=timezone.now()-timedelta(days=10))
        self.assertTrue(candidates('indexnow', 3).filter(pk=job.pk).exists())
        with patch('jobs.management.commands.indexnow_submit.requests.post', return_value=Mock(status_code=202)) as post:
            call_command('indexnow_submit', send=True, stdout=StringIO())
            call_command('indexnow_submit', send=True, stdout=StringIO())
            self.assertEqual(post.call_count, 1)
            job.refresh_from_db()
            self.assertEqual(job.indexing_state['indexnow']['type'], 'URL_DELETED')
            Job.objects.filter(pk=job.pk).update(expires_at=None, last_seen_at=timezone.now(), updated_at=timezone.now())
            call_command('indexnow_submit', send=True, stdout=StringIO())
            self.assertEqual(post.call_count, 2)

    def test_other_provider_state_survives_ack_and_drafts_are_never_removals(self):
        job = self.job()
        acknowledge(job, 'google', fingerprint(job))
        acknowledge(job, 'indexnow', fingerprint(job))
        job.refresh_from_db()
        self.assertEqual(set(job.indexing_state), {'google', 'indexnow'})
        draft = self.job(source='manual', is_active=False)
        self.assertFalse(pending(draft, 'indexnow'))
        self.assertFalse(pending(draft, 'google'))

    def test_google_requires_credentials_and_stops_on_rejection(self):
        job = self.job()
        module = 'jobs.management.commands.ping_google_indexing.Command'
        with patch(module+'.load_credentials', return_value=None):
            with self.assertRaises(CommandError):
                call_command('ping_google_indexing', stdout=StringIO())
        with patch(module+'.load_credentials', return_value={'private_key': 'unused'}), \
             patch(module+'.fetch_access_token', return_value='unused'), patch(module+'.publish', return_value=False) as send:
            with self.assertRaises(CommandError):
                call_command('ping_google_indexing', stdout=StringIO())
            send.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.indexing_state, {})

    def test_google_uses_public_policy_and_deduplicates_accepted_notifications(self):
        public = self.job()
        self.job(quality_score=54)
        self.job(is_active=False, source='manual')
        self.job(location='Remote', country=None)
        module = 'jobs.management.commands.ping_google_indexing.Command'
        with patch(module+'.load_credentials', return_value={'private_key': 'unused'}), \
             patch(module+'.fetch_access_token', return_value='unused'), patch(module+'.publish', return_value=True) as send:
            call_command('ping_google_indexing', stdout=StringIO())
            call_command('ping_google_indexing', stdout=StringIO())
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[1], 'https://remoteimpact.org'+public.get_absolute_url())

    def test_rolled_back_new_job_never_starts_notification_worker(self):
        with patch('jobs.signals.threading.Thread') as thread:
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        self.job()
                        raise RuntimeError('Rollback')
                except RuntimeError:
                    pass
            thread.assert_not_called()

    def test_database_default_allows_old_workers_to_insert_during_rollout(self):
        job = self.job()
        quote = connection.ops.quote_name
        columns = [field.column for field in Job._meta.concrete_fields
                   if field.name not in {'id', 'slug', 'indexing_state'}]
        joined = ', '.join(quote(name) for name in columns)
        with connection.cursor() as cursor:
            cursor.execute(f'INSERT INTO {quote(Job._meta.db_table)} ({joined}, {quote("slug")}) '
                f'SELECT {joined}, %s FROM {quote(Job._meta.db_table)} WHERE {quote("id")} = %s',
                ['legacy-worker-job', job.pk])
        self.assertEqual(Job.objects.get(slug='legacy-worker-job').indexing_state, {})
