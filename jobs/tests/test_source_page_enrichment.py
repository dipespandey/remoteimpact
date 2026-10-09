import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from jobs.models import Category, Job, Organization
from jobs.services.source_page_enrichment import (
    apply_source_page_enrichment,
    fetch_job_page,
    page_matches_title,
    eligible_for_source_enrichment,
)


class SourcePageEnrichmentTests(TestCase):
    @patch("jobs.services.source_page_enrichment.fetch_public_html")
    def test_jsonld_description_survives_script_removal(self, get):
        data = {"@context": "https://schema.org", "@graph": [{
            "@type": "JobPosting", "title": "Senior Python Engineer",
            "description": "<p>Build Python services. Salary range is $120,000 - $150,000 per year.</p>" * 10,
        }]}
        get.return_value = Mock(status="ok", status_code=200, headers={"Content-Type": "text/html"},
                                text='<script type="application/ld+json">' + json.dumps(data) + '</script><main>Loading</main>')
        result = fetch_job_page("https://jobs.example.org/role", title="Senior Python Engineer")
        self.assertEqual(result.status, "ok")
        self.assertIn("$120,000", result.text)

    @patch("jobs.services.source_page_enrichment.fetch_public_html")
    def test_multiple_schema_openings_without_exact_title_are_not_merged(self, get):
        data = [{"@type": "JobPosting", "title": title, "description": "Details " * 100}
                for title in ("Engineer", "Analyst")]
        get.return_value = Mock(status="ok", status_code=200, headers={"Content-Type": "text/html"},
                                text='<script type="application/ld+json">' + json.dumps(data) + '</script>')
        self.assertEqual(fetch_job_page("https://jobs.example.org/roles", title="Director").status,
                         "ambiguous_job_page")

    def test_recent_retryable_failure_has_cooldown(self):
        self.job.raw_data = {"source_page_enrichment": {
            "status": "http_429", "attempted_at": timezone.now().isoformat()}}
        self.assertFalse(eligible_for_source_enrichment(self.job))
        self.job.raw_data["source_page_enrichment"]["attempted_at"] = (
            timezone.now() - timedelta(days=2)).isoformat()
        self.assertTrue(eligible_for_source_enrichment(self.job))

    def test_thin_description_is_prioritized_for_source_recovery(self):
        from jobs.management.commands.enrich_job_source_pages import Command
        self.job.description = "Apply now"
        self.job.save(update_fields=["description"])
        self.assertEqual(Command._candidates(1, force=False)[0].pk, self.job.pk)

    def test_worldwide_requires_explicit_eligibility_not_generic_remote(self):
        from jobs.services.job_scout_service import JobScoutService
        for country, location, expected in (
            ("USA", "Remote (US)", False),
            ("Worldwide", "Remote", False),
            ("Worldwide", "Remote worldwide", True),
            ("USA", "Worldwide - US only", False),
        ):
            with self.subTest(location=location):
                job = SimpleNamespace(title="Software Engineer", country=country, location=location)
                self.assertEqual(JobScoutService._location_compatibility(job, ["Worldwide"]), expected)

    def setUp(self):
        self.organization = Organization.objects.create(name="Impact Labs")
        self.other = Category.objects.create(name="Other", slug="other")
        self.technology = Category.objects.create(name="Technology", slug="technology")
        self.job = Job.objects.create(
            title="Senior Python Engineer",
            slug="source-page-python-engineer",
            organization=self.organization,
            category=self.other,
            source=Job.Source.CLIMATEBASE,
            external_id="source-page-1",
            description="Build durable software for climate work. " * 10,
            application_url="https://jobs.example.org/python-engineer",
            skills=[],
        )

    def test_title_confirmation_requires_specific_job_terms(self):
        self.assertTrue(page_matches_title(
            "Senior Python Engineer",
            "Impact Labs is hiring a Senior Python Engineer for its platform team.",
        ))

    @patch("jobs.services.source_page_enrichment.fetch_public_html")
    def test_fetch_revalidates_and_follows_a_public_redirect(self, get):
        get.return_value = Mock(
            status="ok", status_code=200,
            text="<main>" + ("Senior Python Engineer role details. " * 20) + "</main>",
        )
        result = fetch_job_page("https://jobs.example.org/start")
        self.assertEqual(result.status, "ok")
        get.assert_called_once_with("https://jobs.example.org/start", headers={
            "User-Agent": "RemoteImpactDataQuality/1.0 (+https://remoteimpact.org)",
            "Accept": "text/html,application/xhtml+xml",
        }, max_redirects=3)
        self.assertFalse(page_matches_title(
            "Senior Python Engineer",
            "Welcome to the Impact Labs careers page. Explore our open roles.",
        ))

    def test_only_evidence_backed_sensitive_fields_are_saved(self):
        page = (
            "Senior Python Engineer. Candidates need five years of professional experience. "
            "A bachelor's degree in computer science or equivalent experience is required. "
            "The salary range is $120,000 to $150,000 USD."
        )
        parsed = {
            "impact_area": "technology",
            "experience_level": "senior",
            "experience_evidence": "five years of professional experience",
            "education_level": "bachelor",
            "education_evidence": "bachelor's degree in computer science",
            "salary_min": 120000,
            "salary_max": 150000,
            "salary_currency": "USD",
            "salary_evidence": "salary range is $120,000 to $150,000 USD",
            "skills": ["python"],
        }

        fields = apply_source_page_enrichment(self.job, parsed, page)
        self.job.save(update_fields=fields)
        self.job.refresh_from_db()

        self.assertEqual(self.job.category, self.technology)
        self.assertEqual(self.job.experience_level, "senior")
        self.assertEqual(self.job.education_level, "bachelor")
        self.assertEqual(self.job.salary_min, 120000)
        self.assertEqual(self.job.skills, ["python"])
        provenance = self.job.raw_data["source_page_enrichment"]
        self.assertEqual(provenance["status"], "enriched")
        self.assertIn("salary", provenance["evidence"])

    def test_unsupported_salary_is_not_saved(self):
        parsed = {
            "salary_min": 120000,
            "salary_max": 150000,
            "salary_currency": "USD",
            "salary_evidence": "salary is competitive",
        }

        fields = apply_source_page_enrichment(self.job, parsed, "Senior Python Engineer role.")
        self.job.save(update_fields=fields)
        self.job.refresh_from_db()

        self.assertIsNone(self.job.salary_min)
        self.assertNotIn("salary_min", self.job.raw_data["source_page_enrichment"]["updated_fields"])

    def test_thin_description_is_recovered_from_confirmed_source(self):
        self.job.description = "Apply now"
        page = "Senior Python Engineer. Build Python services for climate projects. " * 10
        fields = apply_source_page_enrichment(self.job, {}, page)
        self.assertEqual(self.job.description, page)
        self.assertIn("description", fields)

    def test_invalid_nonfinite_salary_does_not_crash_enrichment(self):
        fields = apply_source_page_enrichment(self.job, {
            "salary_min": "NaN", "salary_max": "Infinity",
            "salary_evidence": "Salary range is competitive",
        }, "Senior Python Engineer. Salary range is competitive.")
        self.assertNotIn("salary_min", fields)
