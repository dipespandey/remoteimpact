from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from jobs.models import Category, Job, Organization
from jobs.services.importers.common import _mark_job_seen, _upsert_job
from jobs.services.crawlers.base import _jobs_needing_crawl_queryset
from jobs.services.job_enrichment import enrich_job_payload, infer_category_slug, infer_country
from jobs.services.raw_data_extractor import RawDataExtractor
from jobs.services.matching_service import MatchingService
from jobs.services.job_service import JobService
from jobs.constants.skills import normalize_skill_slugs


class JobEnrichmentTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Quality Test Org")

    def test_country_inference_distinguishes_worldwide_and_regions(self):
        self.assertIsNone(infer_country("Remote"))
        self.assertIsNone(infer_country("Unknown location"))
        self.assertEqual(infer_country("Remote worldwide"), "Worldwide")
        self.assertEqual(infer_country("Remote anywhere in the US"), "USA")
        self.assertEqual(infer_country("Remote (Berlin, Germany)"), "Germany")
        self.assertEqual(infer_country("Remote across LATAM"), "Latin America")

    def test_remote_preference_does_not_invent_global_eligibility(self):
        data = RawDataExtractor.extract('climatebase', {'remote_preferences': ['Remote']})
        self.assertEqual(data['location'], 'Remote')
        self.assertNotIn('country', data)

    def test_new_import_preserves_unknown_and_explicit_hiring_country(self):
        base = {
            'title': 'Climate Researcher', 'organization_name': 'Quality Test Org',
            'source': 'climatebase', 'description': 'Research climate resilience. ' * 10,
            'location': 'Remote', 'raw_data': {'remote_preferences': ['Remote']},
        }
        unknown, _ = _upsert_job({**base, 'external_id': 'country-unknown', 'application_url': 'https://example.org/unknown'})
        known, _ = _upsert_job({**base, 'external_id': 'country-known', 'country': 'USA', 'application_url': 'https://example.org/known'})
        self.assertIsNone(unknown.country)
        self.assertEqual(known.country, 'USA')

    def test_refresh_preserves_recovered_salary_and_dead_source_evidence(self):
        payload = {
            "title": "Python Engineer", "organization_name": "Quality Test Org",
            "source": "climatebase", "external_id": "dead-source-quality",
            "description": "Build reliable Python services for climate work. " * 10,
            "application_url": "https://example.org/job/dead",
            "salary_min": 90000, "salary_max": 120000, "salary_currency": "EUR",
        }
        job, _ = _upsert_job(payload)
        job.is_active = False
        job.raw_data = {"source_verification": {"status": "dead"}}
        job.save(update_fields=["is_active", "raw_data"])
        refresh = {key: value for key, value in payload.items() if not key.startswith("salary_")}
        job, _ = _upsert_job(refresh)
        self.assertFalse(job.is_active)
        self.assertEqual(job.salary_min, 90000)
        self.assertEqual(job.salary_currency, "EUR")
        self.assertEqual(job.raw_data["source_verification"]["status"], "dead")
        _mark_job_seen("climatebase", "dead-source-quality")
        job.refresh_from_db()
        self.assertFalse(job.is_active)

    def test_partial_refresh_preserves_description_and_geographic_restrictions(self):
        payload = {
            "title": "Python Engineer", "organization_name": "Quality Test Org",
            "source": "climatebase", "external_id": "partial-quality",
            "description": "Build reliable Python services for climate work. " * 10,
            "location": "Remote (US)", "country": "USA",
            "application_url": "https://example.org/job/partial",
        }
        job, _ = _upsert_job(payload)
        partial = {key: value for key, value in payload.items() if key not in ("location", "country")}
        partial["description"] = "Apply now."
        job, _ = _upsert_job(partial)
        self.assertEqual(job.description, payload["description"])
        self.assertEqual(job.country, "USA")
        self.assertEqual(job.location, "Remote (US)")

    def test_new_import_is_scored_before_it_can_be_listed(self):
        job, created = _upsert_job({
            "title": "Python Engineer",
            "organization_name": "Quality Test Org",
            "description": "Build reliable software for climate work with Python. " * 8,
            "requirements": "Python experience required.",
            "location": "Remote (US)",
            "application_url": "https://example.org/jobs/python-engineer",
            "source": "climatebase",
            "external_id": "quality-scored-import",
        })

        self.assertTrue(created)
        self.assertGreater(job.quality_score, 0)
        self.assertIn("missing_salary", job.quality_issues)

    def test_enrichment_fills_only_missing_structured_fields(self):
        payload = enrich_job_payload({
            "title": "Senior Python Software Engineer",
            "description": "Build renewable energy software with Python and SQL.",
            "requirements": "Bachelor's degree required. Python and SQL.",
            "location": "Remote (US)",
            "experience_level": "mid",
        })

        self.assertEqual(payload["country"], "USA")
        self.assertIn("python", payload["skills"])
        self.assertEqual(payload["experience_level"], "mid")
        self.assertEqual(payload["education_level"], "bachelor")
        self.assertEqual(payload["category_slug"], "climate-environment")

    def test_enrichment_promotes_structured_source_fields(self):
        payload = enrich_job_payload({
            "title": "Policy Associate",
            "description": "Support our policy team.",
            "source": "climatebase",
            "raw_data": {
                "experience_levels": ["Entry Level"],
                "salary_from": 65000,
                "salary_to": 80000,
                "countries": ["United States"],
            },
        })
        self.assertEqual(payload["experience_level"], "entry")
        self.assertEqual(payload["salary_min"], 65000)
        self.assertEqual(payload["salary_max"], 80000)
        self.assertEqual(payload["country"], "USA")

    def test_serialized_null_structured_values_are_treated_as_missing(self):
        payload = enrich_job_payload({
            "title": "Software Engineer",
            "description": "Candidates must have at least 5 years of experience.",
            "experience_level": "null",
            "education_level": "null",
            "country": "null",
            "salary_currency": "null",
            "location": "Remote (US)",
        })

        self.assertEqual(payload["experience_level"], "mid")
        self.assertIsNone(payload["education_level"])
        self.assertEqual(payload["country"], "USA")
        self.assertEqual(payload["salary_currency"], "")

    def test_salary_extraction_requires_compensation_context(self):
        payload = enrich_job_payload({
            "title": "Fundraising Manager",
            "description": "Manage a $2,000,000 grant portfolio.",
            "requirements": "",
        })
        self.assertNotIn("salary_min", payload)

        payload = enrich_job_payload({
            "title": "Fundraising Manager",
            "description": "The salary range is $70,000 - $90,000 per year.",
            "requirements": "",
        })
        self.assertEqual(payload["salary_min"], 70000)
        self.assertEqual(payload["salary_max"], 90000)
        self.assertEqual(payload["salary_currency"], "USD")

    def test_salary_enrichment_rejects_values_that_could_overflow_job_storage(self):
        payload = enrich_job_payload({
            "title": "Senior Executive",
            "description": "",
            "salary_min": 100_000_000,
            "salary_max": 100_000_000,
            "salary_currency": "USD",
        })
        self.assertIsNone(payload["salary_min"])
        self.assertIsNone(payload["salary_max"])
        self.assertEqual(
            payload["raw_data"]["quality_enrichment"]["salary"]["method"],
            "rejected_non_annual_v1",
        )

    def test_hourly_salary_does_not_assume_fulltime_hours(self):
        payload = enrich_job_payload({'title':'Coordinator','description':'Compensation: $25-$30 per hour.','requirements':''})
        self.assertIsNone(payload.get('salary_min'))
        self.assertIsNone(payload.get('salary_max'))

    def test_explicit_years_fill_experience_without_guessing(self):
        explicit = enrich_job_payload({
            "title": "Software Engineer",
            "description": "Candidates must have at least 5 years of relevant experience.",
        })
        vague = enrich_job_payload({
            "title": "Software Engineer",
            "description": "Join our highly experienced engineering team.",
        })
        self.assertEqual(explicit["experience_level"], "mid")
        self.assertIsNone(vague["experience_level"])

    def test_category_rules_prefer_explicit_cause_then_title_function(self):
        self.assertEqual(
            infer_category_slug("Software Engineer", "Build tools for climate mitigation."),
            "climate-environment",
        )
        self.assertEqual(infer_category_slug("Software Engineer"), "technology")
        self.assertEqual(infer_category_slug("Office Coordinator"), "other")

    def test_skill_rules_support_customer_facing_sales_without_single_letter_noise(self):
        skills = MatchingService._extract_skills_from_text(
            "Field sales representative delivering excellent customer service."
        )
        self.assertIn("sales", skills)
        self.assertIn("customer-service", skills)
        self.assertNotIn("r", skills)
        self.assertNotIn("sales", MatchingService._extract_skills_from_text("Salesforce administrator"))
        self.assertEqual(
            MatchingService._extract_skills_from_text("Analyse data with Python, R, and SQL."),
            {"python", "r", "sql"},
        )

        payload = enrich_job_payload({
            "title": "Venue Sales Representative",
            "description": "Join our field sales team and deliver excellent customer service.",
        })
        self.assertEqual(payload["skills"], ["customer-service", "sales"])
        self.assertEqual(
            normalize_skill_slugs(["communication", "customer_success", "not-a-real-skill"]),
            ["communication", "customer-service"],
        )
        self.assertEqual(
            normalize_skill_slugs(["structural-engineering", "C#", "not-a-real-skill"]),
            ["structural-engineering", "csharp"],
        )
        self.assertIn(
            "structural-engineering",
            MatchingService._extract_skills_from_text("Senior Structural Engineer"),
        )
        specialist_skills = MatchingService._extract_skills_from_text(
            "ABAP and FIORI enterprise applications, analytical chemistry with HPLC, "
            "biology prompts for generative AI, and LED electrical fixtures."
        )
        self.assertTrue(
            {
                "abap",
                "fiori",
                "analytical-chemistry",
                "biology",
                "prompt-engineering",
                "electrical-installation",
            }.issubset(specialist_skills)
        )

    def test_seen_upstream_reactivates_job(self):
        job = Job.objects.create(
            title="Current Role",
            slug="current-role-quality-test",
            organization=self.organization,
            description="A current role.",
            requirements="",
            source="idealist",
            external_id="quality-1",
            is_active=False,
            posted_at=timezone.now() - timedelta(days=5),
        )

        _mark_job_seen("idealist", "quality-1")
        job.refresh_from_db()

        self.assertTrue(job.is_active)
        self.assertIsNotNone(job.last_seen_at)

    def test_search_result_does_not_reactivate_closed_ats_job(self):
        job = Job.objects.create(
            title="Closed ATS Role",
            slug="closed-ats-role-quality-test",
            organization=self.organization,
            description="",
            requirements="",
            source="greenhouse",
            external_id="quality-closed-ats",
            is_active=False,
            raw_data={"needs_crawling": False, "crawl_error": "Job not found"},
        )

        _mark_job_seen("greenhouse", "quality-closed-ats")
        job.refresh_from_db()

        self.assertFalse(job.is_active)
        self.assertIsNotNone(job.last_seen_at)

    def test_closed_short_placeholders_are_not_retried_forever(self):
        closed = Job.objects.create(
            title="Closed Placeholder",
            slug="closed-placeholder-quality-test",
            organization=self.organization,
            description="",
            requirements="",
            source="lever",
            external_id="quality-closed-placeholder",
            is_active=False,
            raw_data={"needs_crawling": False, "crawl_error": "Job not found"},
        )
        pending = Job.objects.create(
            title="Pending Placeholder",
            slug="pending-placeholder-quality-test",
            organization=self.organization,
            description="",
            requirements="",
            source="ashby",
            external_id="quality-pending-placeholder",
            is_active=False,
            raw_data={"needs_crawling": True},
        )

        selected_ids = set(
            _jobs_needing_crawl_queryset().values_list("id", flat=True)
        )

        self.assertNotIn(closed.id, selected_ids)
        self.assertIn(pending.id, selected_ids)

    def test_backfill_dry_run_is_non_mutating_then_fills_fields(self):
        Category.objects.create(name="Technology", slug="technology")
        Category.objects.create(name="Other", slug="other")
        job = Job.objects.create(
            title="Python Software Engineer",
            slug="backfill-python-engineer",
            organization=self.organization,
            description="Build services with Python.",
            requirements="Python",
            location="Remote",
            source="greenhouse",
            external_id="quality-2",
        )

        call_command("backfill_job_quality", "--dry-run", stdout=StringIO())
        job.refresh_from_db()
        self.assertIsNone(job.country)
        self.assertEqual(job.skills, [])

        call_command("backfill_job_quality", stdout=StringIO())
        job.refresh_from_db()
        self.assertIsNone(job.country)
        self.assertIn("python", job.skills)
        self.assertEqual(job.category.slug, "technology")

    def test_legacy_r_skill_repair_keeps_only_explicit_r_language_mentions(self):
        noise = Job.objects.create(
            title="Venue Sales Representative",
            slug="legacy-r-noise",
            organization=self.organization,
            description="Work with customers and representatives.",
            source="manual",
            skills=["r"],
        )
        explicit = Job.objects.create(
            title="Data Analyst",
            slug="legacy-r-explicit",
            organization=self.organization,
            description="Use Python, R, and SQL for analysis.",
            source="manual",
            skills=["r", "python"],
        )

        call_command("repair_legacy_skill_tags", stdout=StringIO())
        noise.refresh_from_db()
        explicit.refresh_from_db()

        self.assertEqual(noise.skills, [])
        self.assertEqual(explicit.skills, ["python", "r"])

    def test_public_availability_uses_recent_verification_for_old_jobs(self):
        old_unseen = Job.objects.create(
            title="Stale Role",
            slug="stale-role-quality-test",
            organization=self.organization,
            description="Old role.",
            requirements="",
            posted_at=timezone.now() - timedelta(days=100),
        )
        old_verified = Job.objects.create(
            title="Verified Role",
            slug="verified-role-quality-test",
            organization=self.organization,
            description="Old but still open.",
            requirements="",
            posted_at=timezone.now() - timedelta(days=100),
            last_seen_at=timezone.now() - timedelta(days=1),
        )

        available_ids = set(JobService.get_available_jobs().values_list("id", flat=True))
        self.assertNotIn(old_unseen.id, available_ids)
        self.assertIn(old_verified.id, available_ids)


class DeepSeekSkillBackfillCommandTests(TransactionTestCase):
    def test_deepseek_skill_backfill_keeps_only_taxonomy_slugs_and_records_evidence(self):
        organization = Organization.objects.create(name="DeepSeek Quality Test Org")
        job = Job.objects.create(
            title="Data Analyst",
            slug="deepseek-skill-validation",
            organization=organization,
            description=(
                "Analyse datasets using Python and communicate findings to the organization team. "
                "This role supports reporting, research, and evidence-based operational decisions."
            ),
            source="manual",
            skills=[],
        )

        class Parser:
            provider_name = "deepseek"

            async def parse_batch(self, payloads, **kwargs):
                return [{"skills": ["python", "not-a-real-skill"]} for _ in payloads]

        output = StringIO()
        with patch("jobs.management.commands.backfill_skills.JobParser", return_value=Parser()):
            call_command(
                "backfill_skills", "--include-inactive", "--limit", "1", "--min-description-length", "0",
                stdout=output,
            )

        self.assertIn("Found 1 jobs", output.getvalue())
        self.assertIn("skills extracted", output.getvalue(), output.getvalue())
        job.refresh_from_db()
        self.assertEqual(job.skills, ["python"])
        self.assertEqual(
            job.raw_data["quality_enrichment"]["skills"]["method"], "deepseek_v1"
        )
