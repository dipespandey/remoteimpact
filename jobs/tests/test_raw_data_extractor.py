from django.test import SimpleTestCase

from jobs.services.raw_data_extractor import RawDataExtractor


class RawDataExtractorTests(SimpleTestCase):
    def test_80k_does_not_mix_original_and_converted_salary(self):
        result = RawDataExtractor.extract("80000hours", {
            "salary": "450,000 - 700,000 INR",
            "salary_limit": 7321.4086089388,
        })

        self.assertEqual(result["salary_min"], 450000)
        self.assertEqual(result["salary_max"], 700000)
        self.assertEqual(result["salary_currency"], "INR")

    def test_80k_uses_converted_cap_only_without_original_salary(self):
        result = RawDataExtractor.extract("80000hours", {"salary_limit": 90000})

        self.assertNotIn("salary_min", result)
        self.assertEqual(result["salary_max"], 90000)
        self.assertEqual(result["salary_currency"], "USD")

    def test_80k_annualizes_explicit_monthly_and_hourly_ranges(self):
        monthly = RawDataExtractor.extract("80000hours", {
            "salary": "$3,000 - $4,000 per month",
            "salary_limit": 48000,
        })
        hourly = RawDataExtractor.extract("80000hours", {
            "salary": "$55 - $90 per hour",
            "salary_limit": 187200,
        })

        self.assertEqual(monthly["salary_min"], 36000)
        self.assertEqual(monthly["salary_max"], 48000)
        self.assertEqual(hourly["salary_min"], 114400)
        self.assertEqual(hourly["salary_max"], 187200)

    def test_80k_does_not_treat_grants_as_salary(self):
        result = RawDataExtractor.extract("80000hours", {
            "salary": "$5,000 - $35,000 grant",
            "salary_limit": 35000,
        })
        self.assertNotIn("salary_min", result)
        self.assertNotIn("salary_max", result)
