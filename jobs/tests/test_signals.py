from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from jobs.signals import ping_indexnow_on_job_save


@override_settings(SITE_URL="https://remoteimpact.org")
class IndexNowSignalTest(SimpleTestCase):
    @patch("jobs.signals.threading.Thread")
    def test_updates_do_not_ping_indexnow(self, thread):
        job = Mock(is_active=True)

        ping_indexnow_on_job_save(sender=None, instance=job, created=False)

        thread.assert_not_called()

    @patch("jobs.signals.threading.Thread")
    def test_new_public_jobs_ping_indexnow(self, thread):
        worker = thread.return_value
        job = Mock(is_active=True)
        job.get_absolute_url.return_value = "/jobs/example/"

        ping_indexnow_on_job_save(sender=None, instance=job, created=True)

        thread.assert_called_once()
        self.assertEqual(
            thread.call_args.kwargs["args"],
            ("https://remoteimpact.org/jobs/example/",),
        )
        self.assertTrue(worker.daemon)
        worker.start.assert_called_once_with()
