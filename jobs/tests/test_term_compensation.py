import unittest
from jobs.services.term_compensation import quoted_term_compensation


class TermCompensationTests(unittest.TestCase):
    def test_total_is_not_annual_equivalent(self):
        result = quoted_term_compensation('$100,000–$167,000 USD for the four-month fellowship (equivalent to $300,000–$500,000 annualized).')
        self.assertEqual(result['minimum'], '100000')
        self.assertEqual(result['maximum'], '167000')
        self.assertEqual(result['duration_value'], 4)

    def test_abstains_without_explicit_term(self):
        for text in ['$300,000–$500,000 USD annually', '$40–$60 USD per hour', 'Project budget $100,000', '$100,000–$167,000 USD for the fellowship']:
            with self.subTest(text=text):
                self.assertIsNone(quoted_term_compensation(text))

    def test_invalid_range(self):
        self.assertIsNone(quoted_term_compensation('$167,000–$100,000 USD for the four-month fellowship'))

    def test_budget_is_not_compensation(self):
        self.assertIsNone(quoted_term_compensation('Project budget: $100,000–$167,000 USD for the four-month contract.'))

    def test_annualized_only_quote_is_not_term_total(self):
        self.assertIsNone(quoted_term_compensation('Annualized compensation: $300,000–$500,000 USD for the four-month fellowship.'))

    def test_shared_display_prefers_actual_term(self):
        from types import SimpleNamespace
        from jobs.services.scout_evidence import compensation_display
        job = SimpleNamespace(description='$100,000–$167,000 USD for the four-month fellowship (annualized $300,000–$500,000).')
        self.assertEqual(compensation_display(job), '$100,000 – $167,000 for 4 months')
