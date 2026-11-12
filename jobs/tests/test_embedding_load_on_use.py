from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from jobs.services import embedding_service, scout_retrieval


class EmbeddingLoadOnUseTests(TestCase):
    def setUp(self):
        saved = (embedding_service._model, embedding_service._model_unavailable, embedding_service._load_on_use)
        self.addCleanup(self.restore, saved)
        embedding_service._model, embedding_service._model_unavailable, embedding_service._load_on_use = None, False, False

    def restore(self, saved):
        embedding_service._model, embedding_service._model_unavailable, embedding_service._load_on_use = saved

    @patch.object(embedding_service, "get_model")
    def test_scheduled_runs_with_nothing_to_do_never_load_the_model(self, get_model):
        call_command("send_job_alerts", frequency="instant", stdout=StringIO())
        call_command("read_cvs", stdout=StringIO())
        call_command("run_hiring_assistant", "--screen", stdout=StringIO())
        get_model.assert_not_called()

    @patch.object(embedding_service, "embed", return_value=[0.1, 0.2])
    def test_batch_query_embeds_on_first_use(self, embed):
        embedding_service.load_for_batch()
        self.assertTrue(embedding_service.ready())
        self.assertEqual(scout_retrieval.query_embedding("climate data analyst"), [0.1, 0.2])

    @patch.object(embedding_service, "embed")
    def test_web_request_never_cold_loads_the_model(self, embed):
        self.assertFalse(embedding_service.ready())
        self.assertIsNone(scout_retrieval.query_embedding("climate data analyst"))
        embed.assert_not_called()

    def test_failed_load_turns_query_embeddings_off(self):
        embedding_service.load_for_batch()
        embedding_service._model_unavailable = True
        self.assertFalse(embedding_service.ready())
