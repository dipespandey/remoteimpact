from decimal import Decimal

from django.test import TestCase

from jobs.models import Category, Job, Organization
from jobs.services.job_quality import normalize_salary, score_job


class JobQualityTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Quality Scoring Org")
        self.category = Category.objects.create(name="Technology", slug="technology")

    def job(self, **overrides):
        values = {
            "title": "Python Engineer",
            "slug": f"quality-job-{Job.objects.count()}",
            "organization": self.organization,
            "category": self.category,
            "description": "Build useful Python services. " * 20,
            "requirements": "Python",
            "application_url": "https://example.com/apply",
            "country": "Europe",
            "skills": ["python"],
            "experience_level": "mid",
        }
        values.update(overrides)
        return Job.objects.create(**values)

    def test_idealist_hourly_rate_stays_out_of_annual_fields(self):
        job = self.job(source='idealist', salary_min=Decimal('25'),salary_max=Decimal('30'),raw_data={'salaryPeriod':'HOUR','salaryMinimum':'25','salaryMaximum':'30'})
        normalize_salary(job)
        self.assertIsNone(job.salary_min)
        self.assertIsNone(job.salary_max)
        from jobs.services.scout_evidence import compensation_display
        self.assertEqual(compensation_display(job),'$25 – $30 per hour')
        normalize_salary(job)
        self.assertIsNone(job.salary_min)

    def test_80k_unknown_salary_sentinel_is_removed(self):
        job = self.job(source="80000hours", salary_max=Decimal("9999999"))
        normalize_salary(job)
        self.assertIsNone(job.salary_max)

    def test_impossible_annualized_salary_is_cleared(self):
        job = self.job(
            source="idealist",
            salary_min=Decimal("99999"),
            salary_max=Decimal("100000"),
            raw_data={"salaryPeriod": "HOUR"},
        )
        normalize_salary(job)
        self.assertIsNone(job.salary_min)
        self.assertIsNone(job.salary_max)

    def test_complete_job_passes_scout_quality_threshold(self):
        job = self.job(salary_min=60000, salary_max=80000, education_level="bachelor")
        score, issues = score_job(job)
        self.assertGreaterEqual(score, 55)
        self.assertEqual(issues, [])

    def test_metadata_cannot_mask_a_thin_description(self):
        job = self.job(description="Apply now.", salary_min=60000, salary_max=80000,
                       education_level="bachelor")
        score, issues = score_job(job)
        self.assertLess(score, 55)
        self.assertIn("thin_description", issues)
