from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from jobs.models import Job, Organization
from jobs.signals import _job_embedding_hash


class EmbeddingRefreshTests(TestCase):
    def test_embedding_failure_does_not_block_job_save(self):
        org = Organization.objects.create(name="Embedding Failure Test")
        with patch("jobs.services.embedding_service.embed_job", side_effect=RuntimeError("model unavailable")):
            job = Job.objects.create(title="Offline-safe role", slug="offline-safe-role", organization=org)
        self.assertIsNone(job.embedding)

    def test_stale_refresh_sets_hash_without_embedding_twice(self):
        org = Organization.objects.create(name="Embedding Test")
        job = Job.objects.create(title="Python Engineer", slug="embedding-refresh", organization=org)
        Job.objects.filter(pk=job.pk).update(embedding_content_hash="stale")
        with patch("jobs.management.commands.embed_jobs.embed_job", return_value=[0.0] * 384) as embed:
            with patch("jobs.services.embedding_service.embed_job") as signal_embed:
                call_command("embed_jobs", stale=True, limit=1, stdout=StringIO())
        job.refresh_from_db()
        self.assertEqual(job.embedding_content_hash, _job_embedding_hash(job))
        embed.assert_called_once()
        signal_embed.assert_not_called()
