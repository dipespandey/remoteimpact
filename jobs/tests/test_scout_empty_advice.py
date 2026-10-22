from django.test import SimpleTestCase

from jobs.services.job_scout_service import JobScoutService


class ScoutEmptyAdviceTests(SimpleTestCase):
    def test_incomplete_review_does_not_blame_user_constraints(self):
        advice = JobScoutService.empty_advice(
            {'review_incomplete': True, 'reranker': 'review_unavailable',
             'hard_rejections': {'location': 22}, 'score_rejected': 3},
            {'locations': ['Texas']},
        )
        self.assertIn('could not finish checking', advice)
        self.assertIn('same requirements', advice)
        self.assertNotIn('broader', advice)
        self.assertNotIn('worldwide', advice)

    def test_completed_search_retains_inventory_specific_advice(self):
        advice = JobScoutService.empty_advice(
            {'review_incomplete': False, 'hard_rejections': {'salary': 4}}, {},
        )
        self.assertIn('publish a salary', advice)
        self.assertNotIn('could not finish', advice)
