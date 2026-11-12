from unittest.mock import patch

from django.test import TestCase, override_settings

from jobs.models import Job, Organization
from jobs.services.job_scout_service import JobScoutService

INTENT = "I'm looking for a role within HR Compensation, designing pay structures."


def make_job(org, title, slug, description):
    with patch("jobs.services.embedding_service.embed_job", return_value=None):
        return Job.objects.create(title=title, slug=slug, organization=org, description=description,
                                  location="Remote", application_url=f"https://example.org/{slug}")


@override_settings(DEEPSEEK_API_KEY="test", DEEPSEEK_MODEL="test", SCOUT_MULTISTEP_ENABLED=True,
                   SCOUT_RERANK_ENABLED=True, SCOUT_RERANK_BACKEND="deepseek")
class ReviewPoolTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Pay Equity Org", slug="pay-equity-org")
        self.rewards = make_job(org, "Total Rewards Partner", "total-rewards-partner",
                                "Own salary bands, bonus plans and benefits benchmarking for 400 staff.")
        self.prompts = {}

    def fake_generate(self, prompt, **kwargs):
        self.prompts.setdefault(kwargs.get("stage"), []).append(prompt)
        if kwargs.get("stage") == "parse":
            return ('{"titles":[],"skills":[],"include_keywords":["compensation"],"locations":[],'
                    '"retrieval_terms":["total rewards partner","compensation analyst"]}')
        return '{"decisions":[]}'

    def test_review_sees_retrieved_role_whose_title_shares_no_word_with_the_request(self):
        with patch("jobs.services.scout_workflow.generate", side_effect=self.fake_generate):
            JobScoutService.search_intent(INTENT, limit=3)
        reviewed = " ".join(self.prompts.get("review", []))
        self.assertIn("Total Rewards Partner", reviewed)

    def test_model_search_terms_lead_the_capped_term_list(self):
        from jobs.services.job_scout_service import retrieval_terms
        terms = retrieval_terms(["compliance analyst"], ["total rewards partner"],
                                ["within", "have", "hr compensation", "compensation"])
        self.assertEqual(terms[:3], ["compliance analyst", "total rewards partner", "hr compensation"])
        self.assertEqual(terms[-1], "compensation")
