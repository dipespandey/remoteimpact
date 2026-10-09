from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from jobs.services.ai import AIClient
from jobs.services.guide_generator import _call_deepseek
from jobs.services.llm_parser import PROVIDERS
from jobs.services.json_utils import parse_json_object


class DeepSeekProviderConfigurationTests(SimpleTestCase):
    def test_json_parser_handles_fences_and_trailing_text(self):
        result = parse_json_object('```json\n{"answer": "ok", "text": "{x}"}\n```')

        self.assertEqual(result, {"answer": "ok", "text": "{x}"})

    def test_shared_enrichment_provider_uses_deepseek(self):
        self.assertEqual(set(PROVIDERS), {"deepseek"})
        self.assertEqual(PROVIDERS["deepseek"]["model"], "deepseek-v4-flash")

    @override_settings(DEEPSEEK_API_KEY="test-deepseek-key")
    @patch("jobs.services.ai.OpenAI")
    def test_ai_client_uses_deepseek(self, openai_client):
        client = AIClient()

        self.assertEqual(client.model, "deepseek-v4-flash")
        openai_client.assert_called_once_with(
            api_key="test-deepseek-key", base_url="https://api.deepseek.com"
        )

    @override_settings(DEEPSEEK_API_KEY="test-deepseek-key", DEEPSEEK_MODEL="custom-model")
    @patch("jobs.services.ai.OpenAI")
    def test_ai_client_model_can_be_overridden(self, openai_client):
        client = AIClient()

        self.assertEqual(client.model, "custom-model")

    @override_settings(DEEPSEEK_API_KEY="")
    def test_ai_client_does_not_fall_back_to_another_provider(self):
        with self.assertRaisesMessage(RuntimeError, "DEEPSEEK_API_KEY"):
            AIClient()

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-deepseek-key"}, clear=False)
    @patch("httpx.post")
    def test_application_guides_use_deepseek(self, post):
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": '{"company_intro": "Useful"}'}}]
        }
        post.return_value = response

        result = _call_deepseek("Generate a guide")

        self.assertEqual(result, {"company_intro": "Useful"})
        self.assertEqual(post.call_args.args[0], "https://api.deepseek.com/chat/completions")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek-v4-flash")

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-deepseek-key"}, clear=False)
    @patch("httpx.post")
    def test_application_guides_retry_invalid_json_with_deepseek(self, post):
        invalid_response = Mock()
        invalid_response.json.return_value = {
            "choices": [{"message": {"content": "not json"}}]
        }
        valid_response = Mock()
        valid_response.json.return_value = {
            "choices": [{"message": {"content": '{"company_intro": "Recovered"}'}}]
        }
        post.side_effect = [invalid_response, valid_response]

        result = _call_deepseek("Generate a guide")

        self.assertEqual(result, {"company_intro": "Recovered"})
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs["json"]["temperature"], 0.0)
