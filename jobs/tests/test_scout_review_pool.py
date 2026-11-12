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
        terms = retrieval_terms(["Compliance Analyst"], ["Total Rewards Partner"], ["payroll"],
                                ["within", "have", "HR-compensation", "compensation"])
        self.assertEqual(terms[:4], ["compliance analyst", "total rewards partner", "payroll", "hr compensation"])
        self.assertEqual(terms[-1], "compensation")

    def test_regional_copies_of_one_opening_take_one_review_slot(self):
        from jobs.services.job_scout_service import review_pool
        org = self.rewards.organization
        copies = [make_job(org, "Total Rewards Partner", f"total-rewards-partner-{n}",
                           "Own salary bands, bonus plans and benefits benchmarking for 400 staff.") for n in range(5)]
        other = make_job(org, "Compensation Analyst", "compensation-analyst", "Analyse pay data.")
        pool = review_pool([self.rewards, *copies, other], {}, {})
        self.assertEqual([job.pk for job in pool], [self.rewards.pk, other.pk])

    def test_cross_encoder_order_decides_the_review_slots(self):
        from jobs.services.job_scout_service import review_pool
        org = self.rewards.organization
        others = [make_job(org, f"Unrelated Role {n}", f"unrelated-{n}", f"Different work {n}.") for n in range(14)]
        diagnostics = {}
        with patch("jobs.services.scout_cross_encoder.order", side_effect=lambda query, jobs: list(reversed(jobs))):
            pool = review_pool([*others, self.rewards], {"intent_text": INTENT}, diagnostics)
        self.assertEqual(pool[0].pk, self.rewards.pk)
        self.assertEqual(len(pool), 12)
        self.assertEqual(diagnostics["review_pool"], "cross_encoder")
