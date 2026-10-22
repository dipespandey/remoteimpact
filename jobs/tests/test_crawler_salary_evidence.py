from django.test import SimpleTestCase

from jobs.services.crawlers.base import extract_salary_from_text


class CrawlerSalaryEvidenceTests(SimpleTestCase):
    def test_application_word_counts_are_not_salaries(self):
        self.assertEqual(extract_salary_from_text('401k benefit. Submit 200–400 words.'), {})
        self.assertEqual(extract_salary_from_text('Strategic plan 2030. 200–400 words.'), {})
        self.assertEqual(extract_salary_from_text('Budget: $50. Submit 20,000–40,000 characters.'), {})

    def test_currency_and_thousands_suffix_belong_to_the_matched_amount(self):
        cases = [('$350,000—$850,000 USD', 350000, 850000, 'USD'),
                 ('£67,100 - £89,000', 67100, 89000, 'GBP'),
                 ('Compensation: $150k-$200k', 150000, 200000, 'USD'),
                 ('Salary Range: €80,000 to €120,000', 80000, 120000, 'EUR'),
                 ('CAD 90k–120k', 90000, 120000, 'CAD'),
                 ('Retirement 401k; annual salary $95,000', 95000, 95000, 'USD')]
        for text, low, high, currency in cases:
            with self.subTest(text=text):
                self.assertEqual(extract_salary_from_text(text), {
                    'salary_min': low, 'salary_max': high, 'salary_currency': currency})
        self.assertEqual(extract_salary_from_text('401k and $200 travel allowance'), {})

    def test_currency_is_not_borrowed_from_unrelated_text(self):
        self.assertEqual(extract_salary_from_text('$100 allowance. Salary €80,000–€100,000'), {
            'salary_min': 80000, 'salary_max': 100000, 'salary_currency': 'EUR'})

    def test_base_salary_is_not_ote_commission_funding_or_sales_target(self):
        cases = [
            ('Base salary is targeted to $157k-$192k + commission ($314-$384k OTE)', 157000, 192000, 'USD'),
            ('$157K – $192K • Offers Equity • $157K – $192K Commission • $314-$384k OTE', 157000, 192000, 'USD'),
            ('Compensation: Base $33k annually plus bonus', 33000, 33000, 'USD'),
            ('Salary: up to £65,000, with a variable bonus OTE of £10,000 per annum', None, 65000, 'GBP'),
            ('Salary: $200-260k + equity', 200000, 260000, 'USD'),
        ]
        for text, low, high, currency in cases:
            with self.subTest(text=text):
                self.assertEqual(extract_salary_from_text(text), {
                    'salary_min': low, 'salary_max': high, 'salary_currency': currency})
        for text in [
            'We raised $70M. Close deals at $100k+ ACV. Compensation & benefits: competitive package.',
            'We jointly provide an initial £250k (~$330k / €290k) investment. Minimum guaranteed income of £4,166 per month as a consultancy fee until launch.',
            'Weekly stipend of 3,850 USD. Funding for compute (~$15k/month). Compensation: stipend 3,850 USD per week for four months.',
            'Compensation: $300,000s - $500,000s+, depending on experience.',
            "Compensation: base salary in the high $100,000's - $200'000s plus bonus.",
            'Salary: $158,000-$223,000K plus equity',
            'Salary: $100,000–€150,000',
            '$314-$384k OTE',
            '$250k investment',
            '$100k+ ACV',
        ]:
            with self.subTest(text=text):
                self.assertEqual(extract_salary_from_text(text), {})
