from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.forms import JobAlertForm
from jobs.models import (
    AssistantSubscription,
    Category,
    ConsumerFunnelEvent,
    Job,
    JobAlert,
    JobAlertDelivery,
    Organization,
)
from jobs.services.job_scout_service import JobScoutService
from jobs.services.email_service import email_service
from jobs.services.payment_service import PaymentService
from allauth.socialaccount.models import SocialApp


User = get_user_model()


@override_settings(DEEPSEEK_API_KEY="")
class JobScoutServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="scout", email="scout@example.com", password="test-pass-123"
        )
        self.category = Category.objects.create(name="Climate", slug="climate")
        self.organization = Organization.objects.create(name="Useful Work")
        now = timezone.now()
        self.recent_python = Job.objects.create(
            title="Python Data Engineer",
            slug="python-data-engineer",
            organization=self.organization,
            category=self.category,
            description="Build climate data pipelines with Python and SQL.",
            requirements="Python and SQL",
            skills=["python", "sql"],
            experience_level="mid",
            job_type="full-time",
            location="Remote - Europe",
            country="Europe",
            salary_max=100000,
            posted_at=now - timedelta(hours=4),
        )
        self.old_python = Job.objects.create(
            title="Python Data Engineer",
            slug="older-python-data-engineer",
            organization=self.organization,
            category=self.category,
            description="Build climate data pipelines with Python.",
            requirements="Python",
            skills=["python"],
            experience_level="mid",
            job_type="full-time",
            location="Remote - Europe",
            country="Europe",
            salary_max=90000,
            posted_at=now - timedelta(days=40),
        )
        Job.objects.create(
            title="Python Sales Representative",
            slug="python-sales-representative",
            organization=self.organization,
            category=self.category,
            description="Sell Python training products.",
            requirements="Sales",
            skills=["sales"],
            posted_at=now - timedelta(hours=1),
        )

    def test_fallback_parser_extracts_constraints(self):
        parsed = JobScoutService.parse_intent(
            "I am looking for remote Python or data engineering roles, mid-level, "
            "in Europe, at least $70k. Exclude sales and internships."
        )

        self.assertIn("python", parsed["titles"])
        self.assertIn("mid", parsed["experience_levels"])
        self.assertIn("Europe", parsed["locations"])
        self.assertEqual(parsed["min_salary"], 70000)
        self.assertIn("sales", parsed["exclude_keywords"])

    def test_parser_does_not_turn_qualifiers_into_titles_or_locations(self):
        policy = JobScoutService._fallback_parse(
            "Climate policy analyst or policy advisor roles, remote in Europe, "
            "mid-level or senior."
        )
        health = JobScoutService._fallback_parse(
            "Remote global health data analyst or research analyst roles using SQL."
        )

        self.assertEqual(
            policy["titles"], ["climate policy analyst", "policy advisor"]
        )
        self.assertNotIn("Worldwide", health["locations"])

    def test_category_and_seniority_only_request_is_not_a_title_constraint(self):
        parsed = JobScoutService._fallback_parse(
            "Entry-level remote climate jobs open worldwide."
        )

        self.assertEqual(parsed["titles"], [])
        self.assertIn("entry", parsed["experience_levels"])
        self.assertIn(self.category.slug, parsed["category_slugs"])

    def test_broad_fundraising_phrase_becomes_alternative_role_families(self):
        parsed = JobScoutService._fallback_parse(
            "Nonprofit fundraising and development roles in the US."
        )

        self.assertEqual(parsed["titles"], ["nonprofit fundraising", "development"])

    def test_functional_category_does_not_hide_cause_categorized_role(self):
        communications = Job.objects.create(
            title="Impact Communications Manager",
            slug="impact-communications-manager-cause-category",
            location="Remote worldwide",
            organization=self.organization,
            category=self.category,
            description="Lead marketing and communications for a climate nonprofit.",
            requirements="Communications strategy",
            posted_at=timezone.now() - timedelta(hours=1),
        )
        alert = JobAlert.objects.create(
            user=self.user,
            parsed_preferences={
                "titles": [],
                "skills": [],
                "include_keywords": ["impact", "marketing", "communications"],
                "exclude_keywords": [],
                "locations": ["Worldwide"],
                "job_types": [],
                "experience_levels": [],
                "min_salary": None,
                "category_slugs": ["communications"],
            },
        )

        matches = JobScoutService.get_matches(
            alert, since=timezone.now() - timedelta(days=30)
        )

        self.assertIn(communications.id, [job.id for job in matches])

    def test_operations_intent_rejects_non_operations_health_role(self):
        self.assertTrue(
            JobScoutService._title_matches_intent(
                "Global Healthcare Compliance Operations",
                ["global health operations"],
            )
        )
        self.assertFalse(
            JobScoutService._title_matches_intent(
                "Global Healthcare Sales Director",
                ["global health operations"],
            )
        )

    def test_short_technical_brief_keeps_role_family_separate_from_skills(self):
        parsed = JobScoutService._fallback_parse("java springboot backend developer")

        self.assertEqual(parsed["titles"], ["backend developer"])
        self.assertIn("java", parsed["skills"])
        self.assertIn("backend", parsed["include_keywords"])

    def test_narrative_and_language_are_not_job_titles(self):
        narrative = JobScoutService._fallback_parse("I specialize in taking complicated multi-party partnerships and setting up systems.")
        technical = JobScoutService._fallback_parse("English speaking remote jobs for devops swe or something of the sort")
        self.assertEqual(narrative["titles"], [])
        self.assertEqual(JobScoutService._clean_title_candidates([
            "Development Professional | Natural Resource Management, Rural Livelihoods",
            "Manager", "Specialist",
        ]), [])
        profile = JobScoutService._fallback_parse(
            "Development Professional | Natural Resource Management, Rural Livelihoods "
            "skills in Project Coordination and Administrative Support"
        )
        self.assertEqual(profile["titles"], [])
        self.assertNotIn("english speaking remote", technical["titles"])
        self.assertIn("devops", technical["include_keywords"])
        partner = JobScoutService._fallback_parse(
            "I specialize in taking complicated partnerships and setting up workflows. "
            "I'm looking for my next project-based role in partner operations "
            "somewhere I can build clear toolkits."
        )
        self.assertEqual(partner["titles"], ["partner operations"])

    @override_settings(DEEPSEEK_API_KEY="test-key")
    @patch("jobs.services.ai.AIClient.generate")
    def test_profile_skills_do_not_become_mandatory_titles(self, generate):
        generate.return_value = '{"titles": ["Quantitative Researcher"], "skills": ["Quantitative Research"], "include_keywords": ["Research"]}'
        preferences = JobScoutService.parse_intent("remote skills in Quantitative Research, Research impact work in Climate")
        self.assertEqual(preferences["titles"], [])
        self.assertTrue(preferences["skills"])

    def test_experience_does_not_confuse_international_or_assistant_with_seniority(self):
        international = JobScoutService._fallback_parse("Senior research roles for international organizations")
        assistant = JobScoutService._fallback_parse("I want remote executive assistant roles")
        self.assertNotIn("internship", international["experience_levels"])
        self.assertNotIn("executive", assistant["experience_levels"])
        graduate = JobScoutService._fallback_parse(
            "I am an economics graduate and have been working as a fundraising specialist for a year."
        )
        self.assertEqual(graduate["experience_levels"], ["entry", "mid"])

    def test_exclusions_do_not_swallow_later_positive_preferences(self):
        preferences = JobScoutService._fallback_parse(
            "I want analyst roles. Exclude roles that are heavy on accounting or complex financial modeling "
            "\u2014 I'd rather focus on market research or portfolio support. I also want AI safety work."
        )
        self.assertEqual(preferences["exclude_keywords"], ["accounting", "complex financial modeling"])
        self.assertNotIn("market research", preferences["exclude_keywords"])

    def test_additional_cause_is_an_alternative_to_requested_industry(self):
        Category.objects.create(name="Capital", slug="capital")
        Category.objects.create(name="AI Safety", slug="ai-safety")
        preferences = JobScoutService._fallback_parse(
            "I want analyst roles in venture capital. I am looking in AI safety as well."
        )
        self.assertEqual(preferences["category_match_mode"], "any")
        self.assertIn("capital", preferences["category_slugs"])
        self.assertTrue(JobScoutService._title_matches_intent(
            "Investment Analyst", ["analyst in venture capital"], "Source deals for a venture capital fund."
        ))

    def test_research_and_operations_role_families_keep_functional_evidence(self):
        self.assertTrue(JobScoutService._title_matches_intent(
            "Research Analyst", ["qualitative researcher"], "Conduct qualitative research and interview studies."
        ))
        self.assertTrue(JobScoutService._title_matches_intent(
            "Partnerships Manager", ["partner operations specialist"], "Own partner operations and onboarding workflows."
        ))
        self.assertFalse(JobScoutService._title_matches_intent(
            "Sales Manager", ["partner operations specialist"], "Sell products to partners."
        ))
        self.assertFalse(JobScoutService._title_matches_intent(
            "People Operations Manager", ["partner operations specialist"],
            "Partner with HR to run payroll and employee onboarding."
        ))

    def test_retrieval_keeps_relevant_role_beyond_newest_400_keyword_hits(self):
        now = timezone.now()
        Job.objects.bulk_create([
            Job(title="Sales Associate", slug=f"keyword-noise-{i}", organization=self.organization,
                description="Work for a nonprofit", posted_at=now) for i in range(405)
        ])
        relevant = Job.objects.create(
            title="Software Engineer", slug="older-relevant-engineer", organization=self.organization,
            description="Build nonprofit tools", posted_at=now - timedelta(days=10),
        )
        matches = JobScoutService.get_matches_for_preferences({
            "titles": ["software engineer"], "include_keywords": ["nonprofit"],
        }, since=now - timedelta(days=30))
        self.assertIn(relevant.pk, [job.pk for job in matches])

    def test_investment_brief_rejects_health_role_sharing_generic_risk_skill(self):
        health = Job.objects.create(
            title="Health Risk Assessment Specialist", slug="health-risk-scout",
            organization=self.organization, category=self.category,
            description="Public health research and risk management", skills=["risk-management"],
            posted_at=timezone.now(),
        )
        finance = Job.objects.create(
            title="Impact Investment Analyst", slug="investment-analyst-scout",
            organization=self.organization, category=self.category,
            description="Investment management and portfolio management for climate funds",
            skills=["investment-management", "portfolio-management"], posted_at=timezone.now(),
        )
        matches = JobScoutService.get_matches_for_preferences({
            "titles": [], "skills": ["investment management", "portfolio management", "risk management"],
            "include_keywords": ["climate", "impact"], "category_slugs": [self.category.slug],
        })
        self.assertIn(finance.pk, [job.pk for job in matches])
        self.assertNotIn(health.pk, [job.pk for job in matches])

    def test_role_brief_with_python_rejects_software_job_without_python_evidence(self):
        javascript = Job.objects.create(
            title="Software Engineer", slug="javascript-not-python-scout",
            organization=self.organization, description="Build JavaScript applications.",
            skills=["python", "software-engineering"], posted_at=timezone.now(),
        )
        python = Job.objects.create(
            title="Software Engineer", slug="actual-python-software-scout",
            organization=self.organization, description="Build applications with Python.",
            skills=["python"], posted_at=timezone.now(),
        )
        matches = JobScoutService.get_matches_for_preferences({
            "titles": ["software engineer"], "skills": ["Python", "software-engineering"],
        })
        self.assertIn(python.pk, [job.pk for job in matches])
        self.assertNotIn(javascript.pk, [job.pk for job in matches])

    def test_finance_title_brief_rejects_nonfinancial_roles_with_noisy_tags(self):
        noise = Job.objects.create(
            title="Portfolio Delivery Manager - Environmental Remediation",
            slug="nonfinancial-portfolio-scout", organization=self.organization,
            description="Manage remediation projects and environmental assessments.",
            skills=["investment-management", "portfolio-management", "strategic-finance"],
            posted_at=timezone.now(),
        )
        finance = Job.objects.create(
            title="Impact Investment Portfolio Manager", slug="financial-portfolio-scout",
            organization=self.organization,
            description="Investment management and portfolio management for climate funds.",
            skills=["investment-management", "portfolio-management"], posted_at=timezone.now(),
        )
        matches = JobScoutService.get_matches_for_preferences({
            "titles": ["Impact Investment Portfolio Manager"],
            "skills": ["investment management", "portfolio management", "strategic finance"],
        })
        self.assertIn(finance.pk, [job.pk for job in matches])
        self.assertNotIn(noise.pk, [job.pk for job in matches])

    def test_inflected_role_family_is_normalized_to_listing_title(self):
        parsed = JobScoutService._fallback_parse(
            "Remote software engineering roles using Python in Europe"
        )

        self.assertEqual(parsed["titles"], ["software engineer"])
        self.assertIn("python", parsed["skills"])
        self.assertEqual(parsed["locations"], ["Europe"])

    def test_short_technical_brief_returns_a_matching_backend_opening(self):
        job = Job.objects.create(
            title="Java Backend Developer",
            slug="java-backend-developer-scout-match",
            organization=self.organization,
            description="Build Java and Spring Boot services for a mission-driven team.",
            skills=["java", "spring-boot", "backend"],
            location="Remote",
            posted_at=timezone.now() - timedelta(hours=2),
            is_active=True,
        )

        matches = JobScoutService.get_matches_for_preferences(
            JobScoutService._fallback_parse("java springboot backend developer"),
            since=timezone.now() - timedelta(days=30),
        )

        self.assertIn(job.id, [match.id for match in matches])

    def test_explicit_location_mismatch_and_plural_exclusion_are_rejected(self):
        us_job = Job.objects.create(
            title="Python Software Engineer",
            slug="us-python-software-engineer",
            organization=self.organization,
            description="Build services with Python.",
            skills=["python"],
            location="Remote (US)",
            country="USA",
            posted_at=timezone.now() - timedelta(hours=1),
        )
        internship = Job.objects.create(
            title="Python Software Engineering Internship",
            slug="python-software-internship",
            organization=self.organization,
            description="Build services with Python in Europe.",
            skills=["python"],
            location="Remote (Europe)",
            country="Europe",
            posted_at=timezone.now() - timedelta(hours=1),
        )
        africa_job = Job.objects.create(
            title="Climate Policy Analyst - Any African Country",
            slug="africa-climate-policy-analyst",
            organization=self.organization,
            description="Climate policy research.",
            location="Remote",
            posted_at=timezone.now() - timedelta(hours=1),
        )
        alert = JobAlert.objects.create(
            user=self.user,
            parsed_preferences={
                "titles": ["software engineer"],
                "skills": ["python"],
                "include_keywords": [],
                "exclude_keywords": ["internships"],
                "locations": ["Europe"],
                "job_types": [],
                "experience_levels": [],
                "min_salary": None,
                "category_slugs": [],
            },
        )

        matches = JobScoutService.get_matches(
            alert, since=timezone.now() - timedelta(days=30)
        )
        match_ids = {job.id for job in matches}
        self.assertNotIn(us_job.id, match_ids)
        self.assertNotIn(internship.id, match_ids)
        self.assertFalse(
            JobScoutService._location_compatibility(africa_job, ["Europe"])
        )

    def test_skill_led_europe_query_returns_europe_and_worldwide_jobs(self):
        worldwide = Job.objects.create(
            title="Python Backend Engineer",
            slug="worldwide-python-backend-engineer",
            organization=self.organization,
            description="Build backend services with Python.",
            requirements="Python",
            skills=["python"],
            location="Remote worldwide",
            country="Worldwide",
            posted_at=timezone.now() - timedelta(days=8),
        )
        unrelated = Job.objects.create(
            title="Operations Coordinator",
            slug="europe-operations-coordinator-without-python",
            organization=self.organization,
            description="Coordinate programs across Europe.",
            location="Remote (Europe)",
            country="Europe",
            posted_at=timezone.now() - timedelta(hours=1),
        )
        preferences = JobScoutService._fallback_parse(
            "remote jobs in europe python"
        )
        alert = JobAlert.objects.create(
            user=self.user,
            intent_text="remote jobs in europe python",
            parsed_preferences=preferences,
        )

        matches = JobScoutService.get_matches(
            alert, since=timezone.now() - timedelta(days=30)
        )
        match_ids = {job.id for job in matches}

        self.assertIn(self.recent_python.id, match_ids)
        self.assertIn(worldwide.id, match_ids)
        self.assertNotIn(unrelated.id, match_ids)

    def test_skill_match_in_title_ranks_above_body_only_match(self):
        title_match = Job(
            title="Python Backend Engineer",
            description="Build reliable services for a mission-driven team.",
            skills=[],
            posted_at=timezone.now() - timedelta(days=2),
        )
        body_match = Job(
            title="Operations Coordinator",
            description="Coordinate programs; Python experience is helpful.",
            skills=[],
            posted_at=timezone.now() - timedelta(hours=1),
        )
        preferences = JobScoutService._fallback_parse("remote jobs using Python")

        title_score, _ = JobScoutService._score_job(title_match, preferences, timezone.now())
        body_score, _ = JobScoutService._score_job(body_match, preferences, timezone.now())

        self.assertGreater(title_score, body_score)

    def test_generic_title_word_does_not_make_unrelated_manager_relevant(self):
        unrelated = Job.objects.create(
            title="Operations Manager",
            slug="operations-manager-with-comms",
            organization=self.organization,
            description="Supports communications at a nonprofit organization.",
            posted_at=timezone.now() - timedelta(hours=1),
        )
        relevant = Job.objects.create(
            title="Communications Manager",
            slug="communications-manager",
            organization=self.organization,
            description="Communications for a nonprofit organization.",
            posted_at=timezone.now() - timedelta(hours=2),
        )
        alert = JobAlert.objects.create(
            user=self.user,
            parsed_preferences={
                "titles": ["communications manager"],
                "skills": [],
                "include_keywords": ["communications", "nonprofit"],
                "exclude_keywords": [],
                "locations": [],
                "job_types": [],
                "experience_levels": [],
                "min_salary": None,
                "category_slugs": [],
            },
        )

        matches = JobScoutService.get_matches(
            alert, since=timezone.now() - timedelta(days=30)
        )
        match_ids = {job.id for job in matches}
        self.assertIn(relevant.id, match_ids)
        self.assertNotIn(unrelated.id, match_ids)

    def test_project_manager_intent_rejects_sales_manager_for_projects(self):
        self.assertTrue(
            JobScoutService._title_matches_intent(
                "Senior Energy Project Manager", ["project manager"]
            )
        )
        self.assertFalse(
            JobScoutService._title_matches_intent(
                "Sales Manager, Large Projects", ["project manager"]
            )
        )

    def test_contract_domain_language_is_not_an_employment_type(self):
        legal = JobScoutService._fallback_parse(
            "Legal work involving contract drafting, research, and arbitration."
        )
        explicit = JobScoutService._fallback_parse(
            "I want contract roles doing legal research and arbitration."
        )

        self.assertNotIn("contract", legal["job_types"])
        self.assertIn("contract", explicit["job_types"])

    def test_legal_intent_rejects_generic_analyst_and_advisor_titles(self):
        desired = ["legal researcher", "legal advisor"]

        self.assertTrue(
            JobScoutService._title_matches_intent(
                "Legal Researcher", desired, "Research on arbitration and contracts."
            )
        )
        self.assertTrue(
            JobScoutService._title_matches_intent(
                "Staff Attorney",
                desired,
                "Conduct legal research and draft contracts for clients.",
            )
        )
        self.assertFalse(
            JobScoutService._title_matches_intent(
                "Data Analyst", desired, "Analyze climate markets and energy data."
            )
        )
        self.assertFalse(
            JobScoutService._title_matches_intent(
                "Climate Policy Advisor", desired, "Advise on climate policy."
            )
        )
        self.assertFalse(
            JobScoutService._title_matches_intent(
                "Growth Marketer (6-Month Contract)",
                ["contract drafter", *desired],
                "Run campaigns as a short-term contractor.",
            )
        )

    def test_climate_organization_language_maps_to_climate_category(self):
        parsed = JobScoutService._fallback_parse(
            "Remote software engineer roles at climate organizations."
        )

        self.assertIn(self.category.slug, parsed["category_slugs"])

    def test_legal_title_family_scores_above_unrequested_internship(self):
        preferences = {
            "titles": ["legal researcher", "legal advisor"],
            "skills": ["legal research"],
            "include_keywords": ["legal research", "drafting"],
            "exclude_keywords": [],
            "locations": [],
            "job_types": [],
            "experience_levels": [],
            "min_salary": None,
            "category_slugs": [],
        }
        attorney = Job(
            title="Staff Attorney",
            description="Conduct legal research and draft filings.",
            posted_at=timezone.now() - timedelta(days=2),
        )
        internship = Job(
            title="Legal Internship",
            description="Conduct legal research and draft filings.",
            posted_at=timezone.now() - timedelta(days=1),
        )

        attorney_score, attorney_reasons = JobScoutService._score_job(
            attorney, preferences, timezone.now()
        )
        internship_score, _ = JobScoutService._score_job(
            internship, preferences, timezone.now()
        )

        self.assertGreater(attorney_score, internship_score)
        self.assertIn("Legal role aligned with your brief", attorney_reasons)

    def test_minimum_salary_excludes_unknown_and_below_minimum_jobs(self):
        unknown_salary = Job.objects.create(
            title="Software Engineer",
            slug="unknown-salary-software-engineer",
            organization=self.organization,
            description="Build software using Python.",
            skills=["python"],
            posted_at=timezone.now() - timedelta(hours=1),
        )
        below_minimum = Job.objects.create(
            title="Software Engineer",
            slug="below-minimum-software-engineer",
            organization=self.organization,
            description="Build software using Python.",
            skills=["python"],
            salary_max=60000,
            posted_at=timezone.now() - timedelta(hours=1),
        )
        alert = JobAlert.objects.create(
            user=self.user,
            parsed_preferences={
                "titles": ["software engineer"],
                "skills": ["python"],
                "include_keywords": [],
                "exclude_keywords": [],
                "locations": [],
                "job_types": [],
                "experience_levels": [],
                "min_salary": 70000,
                "category_slugs": [],
            },
        )

        matches = JobScoutService.get_matches(
            alert, since=timezone.now() - timedelta(days=30)
        )

        match_ids = {job.id for job in matches}
        self.assertNotIn(unknown_salary.id, match_ids)
        self.assertNotIn(below_minimum.id, match_ids)

    @override_settings(DEEPSEEK_API_KEY="test-key")
    @patch("jobs.services.ai.AIClient.generate")
    def test_ai_parser_does_not_invent_unrequested_constraints(self, generate):
        generate.return_value = """{
            "titles": ["Python Data Engineer", "Senior Data Engineer"],
            "skills": ["Python", "SQL", "Apache Spark"],
            "include_keywords": ["data pipelines"],
            "exclude_keywords": ["sales"],
            "locations": ["Europe"],
            "job_types": [],
            "experience_levels": ["senior"],
            "min_salary": 90000,
            "category_slugs": []
        }"""

        parsed = JobScoutService.parse_intent(
            "Remote Python data engineering roles in Europe, at least $70k. Exclude sales."
        )

        self.assertIn("Python Data Engineer", parsed["titles"])
        self.assertNotIn("Senior Data Engineer", parsed["titles"])
        self.assertEqual(parsed["skills"], ["Python", "data-engineering"])
        self.assertNotIn("SQL", parsed["skills"])
        self.assertEqual(parsed["experience_levels"], [])
        self.assertEqual(parsed["min_salary"], 70000)

    def test_matching_is_relevant_fresh_and_deduplicated(self):
        alert = JobAlert.objects.create(
            user=self.user,
            name="Data work",
            intent_text="Remote Python data engineering in Europe, excluding sales",
            parsed_preferences={
                "titles": ["python data engineer"],
                "skills": ["python", "sql"],
                "include_keywords": ["data engineering", "climate"],
                "exclude_keywords": ["sales"],
                "locations": ["Europe"],
                "job_types": ["full-time"],
                "experience_levels": ["mid"],
                "min_salary": 70000,
                "category_slugs": ["climate"],
            },
        )

        matches = JobScoutService.get_matches(
            alert, since=timezone.now() - timedelta(days=180)
        )
        self.assertEqual([job.id for job in matches], [self.recent_python.id, self.old_python.id])
        self.assertIn("Published recently", matches[0].scout_reasons)

        JobAlertDelivery.objects.create(alert=alert, job=self.recent_python)
        deduplicated = JobScoutService.get_matches(
            alert, since=timezone.now() - timedelta(days=180)
        )
        self.assertNotIn(self.recent_python.id, [job.id for job in deduplicated])


@override_settings(DEEPSEEK_API_KEY="")
class JobScoutFlowTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="flow", email="flow@example.com", password="test-pass-123"
        )
        self.organization = Organization.objects.create(name="Flow Org")
        self.job = Job.objects.create(
            title="Software Engineer",
            slug="flow-software-engineer",
            organization=self.organization,
            description="Build software for public benefit using Python.",
            requirements="Python",
            skills=["python"],
            posted_at=timezone.now() - timedelta(hours=2),
        )
        self.client.force_login(self.user)
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider,
                provider_id=provider_id,
                name=(provider_id or provider).title(),
                client_id=f"{provider_id or provider}-client",
                secret=f"{provider_id or provider}-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(site)
        self.preferences = {
            "titles": ["software engineer"],
            "skills": ["python"],
            "include_keywords": [],
            "exclude_keywords": [],
            "locations": [],
            "job_types": [],
            "experience_levels": [],
            "min_salary": None,
            "category_slugs": [],
        }

    def test_homepage_hero_routes_intent_into_job_scout(self):
        response = self.client.get(reverse("jobs:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="hero-job-scout"')
        self.assertContains(response, 'id="hero-job-scout-launcher"')
        self.assertContains(response, 'id="job-scout-composer"')
        self.assertContains(response, 'id="job-scout-composer-intent"')
        self.assertContains(response, 'maxlength="2000"')
        self.assertContains(response, reverse("jobs:job_scout_preview"))
        self.assertContains(response, 'name="source" value="home_hero_modal"')
        self.assertContains(response, "job_scout_composer_submit")
        self.assertContains(response, "suppressLoginPopup")

    def test_anonymous_preview_shows_matches_and_preserves_intent_for_signup(self):
        self.client.logout()
        self.job.location = "Remote worldwide"
        self.job.save(update_fields=["location"])
        intent = "Remote software engineering roles using Python worldwide."

        response = self.client.get(
            reverse("jobs:job_scout_preview"),
            {"intent": intent, "source": "home_hero"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["intent"], intent)
        self.assertEqual(response.context["preview_jobs"], [self.job])
        self.assertContains(response, "Continue to my free Scout")
        self.assertIn("/alerts/?intent=", response.context["next_path"])
        self.assertEqual(
            ConsumerFunnelEvent.objects.filter(
                event=ConsumerFunnelEvent.Event.SCOUT_PREVIEWED
            ).count(),
            1,
        )

        signup_start = self.client.get(
            reverse("jobs:job_scout_signup_start"),
            {"next": response.context["next_path"], "source": "home_hero"},
        )
        self.assertRedirects(
            signup_start,
            f"{reverse('account_signup')}?{urlencode({'next': response.context['next_path']})}",
            fetch_redirect_response=False,
        )
        self.assertTrue(
            ConsumerFunnelEvent.objects.filter(
                event=ConsumerFunnelEvent.Event.SCOUT_SIGNUP_STARTED,
                metadata__source="home_hero",
            ).exists()
        )

        unsafe = self.client.get(
            reverse("jobs:job_scout_signup_start"), {"next": "https://example.com"}
        )
        self.assertTrue(unsafe.url.startswith(reverse("account_signup")))
        self.assertIn("next=%2Falerts%2F", unsafe.url)

        self.client.get(
            reverse("jobs:job_scout_preview"),
            {"intent": intent, "source": "home_hero"},
        )
        self.assertEqual(
            ConsumerFunnelEvent.objects.filter(
                event=ConsumerFunnelEvent.Event.SCOUT_PREVIEWED
            ).count(),
            1,
        )

    def test_preview_rejects_an_underspecified_brief(self):
        response = self.client.get(
            reverse("jobs:job_scout_preview"), {"intent": "python"}
        )

        self.assertRedirects(response, reverse("jobs:home"))

    def test_scout_signup_handoff_keeps_the_conversion_promise(self):
        self.client.logout()
        next_path = "/alerts/?intent=Remote+Python+engineering+roles&source=scout_preview"

        response = self.client.get(reverse("account_signup"), {"next": next_path})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create an account to finish your Scout")
        self.assertContains(response, "Continue to my Scout")
        self.assertEqual(response.context["redirect_field_value"], next_path)

    def test_homepage_intent_is_prefilled_on_job_scout_page(self):
        intent = "Remote software engineering roles using Python in Europe."

        response = self.client.get(
            reverse("jobs:job_alerts"),
            {"intent": intent, "source": "home_hero"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["intent_text"], intent)
        self.assertContains(response, "job_scout_landing")

        self.client.get(
            reverse("jobs:job_alerts"),
            {"intent": intent, "source": "home_hero"},
        )
        landing_events = ConsumerFunnelEvent.objects.filter(
            user=self.user,
            event=ConsumerFunnelEvent.Event.SCOUT_LANDED,
        )
        self.assertEqual(landing_events.count(), 1)
        self.assertEqual(landing_events.first().metadata["source"], "home_hero")

    def test_homepage_intent_survives_login_redirect(self):
        intent = "Remote software engineering roles using Python in Europe."
        self.client.logout()

        response = self.client.get(
            reverse("jobs:job_alerts"),
            {"intent": intent, "source": "home_hero"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
        self.assertIn("next=/alerts/%3Fintent%3D", response.url)
        self.assertIn("source%3Dhome_hero", response.url)

    @patch("jobs.views.job_alerts.email_service.send_job_alert", return_value=True)
    @patch("jobs.views.job_alerts.JobScoutService.parse_intent")
    def test_scout_creation_records_its_acquisition_source(self, parse_intent, send_email):
        parse_intent.return_value = self.preferences

        self.client.post(
            f"{reverse('jobs:job_alert_create')}?source=ignored-query-string",
            {
                "name": "Engineering",
                "intent_text": "I want remote software engineering roles using Python.",
                "frequency": "once",
                "source": "day3_scout_email",
            },
        )

        event = ConsumerFunnelEvent.objects.filter(
            event=ConsumerFunnelEvent.Event.SCOUT_CREATED
        ).latest("created_at")
        self.assertEqual(event.metadata["source"], "day3_scout_email")

    @patch("jobs.views.job_alerts.email_service.send_job_alert", return_value=True)
    @patch("jobs.views.job_alerts.JobScoutService.parse_intent")
    def test_create_scout_interprets_emails_and_records_delivery(self, parse_intent, send_email):
        parse_intent.return_value = self.preferences
        response = self.client.post(
            reverse("jobs:job_alert_create"),
            {
                "name": "Engineering",
                "intent_text": "I want remote software engineering roles using Python.",
                "frequency": "once",
            },
        )

        alert = JobAlert.objects.get(user=self.user)
        self.assertRedirects(
            response,
            f"{reverse('jobs:job_alerts')}?created={alert.pk}",
            fetch_redirect_response=False,
        )
        self.assertEqual(alert.parsed_preferences, self.preferences)
        self.assertEqual(alert.frequency, JobAlert.Frequency.ONCE)
        self.assertFalse(alert.is_active)
        self.assertTrue(JobAlertDelivery.objects.filter(alert=alert, job=self.job).exists())
        self.assertTrue(
            ConsumerFunnelEvent.objects.filter(
                user=self.user,
                alert=alert,
                event=ConsumerFunnelEvent.Event.SCOUT_CREATED,
            ).exists()
        )
        self.assertTrue(
            ConsumerFunnelEvent.objects.filter(
                user=self.user,
                alert=alert,
                event=ConsumerFunnelEvent.Event.INITIAL_EMAIL_SENT,
            ).exists()
        )
        subscription = AssistantSubscription.get_or_create_for_user(self.user)
        self.assertIsNotNone(subscription.free_job_scout_used_at)
        send_email.assert_called_once()

        page = self.client.get(reverse("jobs:job_alerts"))
        self.assertContains(page, "Your free search is complete")
        self.assertNotContains(page, "Create a new scout")

    @patch("jobs.views.job_alerts.email_service.send_job_alert", return_value=True)
    @patch("jobs.views.job_alerts.JobScoutService.parse_intent")
    def test_repeated_submission_reuses_scout_and_sends_one_email(self, parse_intent, send_email):
        parse_intent.return_value = self.preferences
        payload = {"intent_text": "I want remote software engineering roles using Python.", "frequency": "once"}
        first = self.client.post(reverse("jobs:job_alert_create"), payload)
        second = self.client.post(reverse("jobs:job_alert_create"), payload)
        self.assertEqual(first.url, second.url)
        self.assertEqual(JobAlert.objects.filter(user=self.user).count(), 1)
        send_email.assert_called_once()
        parse_intent.assert_called_once()

    def test_preview_preserves_request_at_end_of_long_background(self):
        intent = "I have worked with partners on complex workflows. " * 12 + "I want remote partner operations roles."
        response = self.client.get(reverse("jobs:job_scout_preview"), {"intent": intent})
        self.assertEqual(response.context["intent"], intent)
        self.assertIn("I want remote partner operations roles.", response.context["intent"])

    @patch("jobs.views.job_alerts.email_service.send_job_alert", return_value=True)
    @patch("jobs.views.job_alerts.JobScoutService.parse_intent")
    def test_free_plan_is_limited_to_one_one_time_scout(self, parse_intent, send_email):
        parse_intent.return_value = self.preferences
        subscription = AssistantSubscription.get_or_create_for_user(self.user)
        subscription.free_job_scout_used_at = timezone.now()
        subscription.save(update_fields=["free_job_scout_used_at"])

        self.client.post(
            reverse("jobs:job_alert_create"),
            {
                "name": "Second",
                "intent_text": "I want another software engineering role alert.",
                "frequency": "once",
            },
        )
        self.assertEqual(JobAlert.objects.filter(user=self.user).count(), 0)
        send_email.assert_not_called()

    @patch("jobs.views.job_alerts.email_service.send_job_alert", return_value=True)
    @patch("jobs.views.job_alerts.JobScoutService.parse_intent")
    @patch("jobs.views.job_alerts.JobScoutService.get_matches", return_value=[])
    def test_empty_free_scout_does_not_consume_the_one_time_credit(
        self, get_matches, parse_intent, send_email
    ):
        parse_intent.return_value = self.preferences

        response = self.client.post(
            reverse("jobs:job_alert_create"),
            {
                "name": "No current match",
                "intent_text": "I want remote software engineering roles using Python.",
                "frequency": "once",
            },
        )

        alert = JobAlert.objects.get(user=self.user)
        subscription = AssistantSubscription.get_or_create_for_user(self.user)
        self.assertIsNone(subscription.free_job_scout_used_at)
        self.assertFalse(alert.is_active)
        self.assertRedirects(
            response,
            f"{reverse('jobs:job_alerts')}?created={alert.pk}",
            fetch_redirect_response=False,
        )
        get_matches.assert_called_once()
        send_email.assert_not_called()
        self.assertTrue(
            ConsumerFunnelEvent.objects.filter(
                user=self.user,
                alert=alert,
                event=ConsumerFunnelEvent.Event.INITIAL_MATCHES_EMPTY,
            ).exists()
        )

    def test_recurring_frequencies_are_only_offered_to_pro(self):
        free_form = JobAlertForm(user=self.user)
        self.assertEqual(
            dict(free_form.fields["frequency"].choices),
            {"once": "One-time matching email"},
        )
        self.assertNotIn("instant", dict(free_form.fields["frequency"].choices))
        self.assertNotIn("daily", dict(free_form.fields["frequency"].choices))

        subscription = AssistantSubscription.get_or_create_for_user(self.user)
        subscription.is_subscribed = True
        subscription.expires_at = timezone.now() + timedelta(days=30)
        subscription.save()
        pro_form = JobAlertForm(user=self.user)
        self.assertIn("instant", dict(pro_form.fields["frequency"].choices))
        self.assertIn("daily", dict(pro_form.fields["frequency"].choices))

    def test_free_user_cannot_forge_a_daily_scout(self):
        response = self.client.post(
            reverse("jobs:job_alert_create"),
            {
                "name": "Forged recurring scout",
                "intent_text": "I want remote software engineering roles using Python.",
                "frequency": "daily",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "frequency", "Select a valid choice. daily is not one of the available choices.")
        self.assertFalse(JobAlert.objects.filter(user=self.user).exists())

    @patch("jobs.management.commands.send_job_alerts.email_service.send_job_alert")
    def test_scheduler_skips_recurring_alerts_for_free_users(self, send_email):
        JobAlert.objects.create(
            user=self.user,
            name="Legacy daily scout",
            intent_text="Remote software engineering roles using Python.",
            frequency=JobAlert.Frequency.DAILY,
            is_active=True,
        )

        call_command(
            "send_job_alerts",
            frequency="daily",
            force=True,
            stdout=StringIO(),
        )

        send_email.assert_not_called()

    def test_email_unsubscribe_only_pauses_its_job_scout(self):
        alert = JobAlert.objects.create(
            user=self.user,
            name="Engineering",
            intent_text="Remote software engineering roles using Python.",
        )
        other_alert = JobAlert.objects.create(
            user=self.user,
            name="Other",
            intent_text="Remote product management roles in Europe.",
            is_active=False,
        )
        url = email_service._get_job_alert_unsubscribe_url(alert)

        response = self.client.get(url.replace("https://remoteimpact.org", ""))

        self.assertEqual(response.status_code, 200)
        alert.refresh_from_db()
        other_alert.refresh_from_db()
        self.assertFalse(alert.is_active)
        self.assertFalse(other_alert.is_active)
        self.assertContains(response, "Engineering")

    def test_user_can_rate_own_delivered_match(self):
        alert = JobAlert.objects.create(
            user=self.user,
            name="Engineering",
            intent_text="Remote software engineering roles using Python.",
            frequency=JobAlert.Frequency.ONCE,
            is_active=False,
        )
        delivery = JobAlertDelivery.objects.create(
            alert=alert,
            job=self.job,
            match_score=88,
            match_reasons=["Role title closely matches what you described"],
        )

        response = self.client.post(
            reverse("jobs:job_alert_feedback", args=[delivery.pk]),
            {"feedback": JobAlertDelivery.Feedback.RELEVANT},
        )

        self.assertRedirects(
            response,
            f"{reverse('jobs:job_alerts')}?created={alert.pk}",
            fetch_redirect_response=False,
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.feedback, JobAlertDelivery.Feedback.RELEVANT)
        self.assertIsNotNone(delivery.feedback_at)
        self.assertTrue(
            ConsumerFunnelEvent.objects.filter(
                user=self.user,
                alert=alert,
                event=ConsumerFunnelEvent.Event.MATCH_RELEVANT,
                metadata__delivery_id=delivery.pk,
            ).exists()
        )

        page = self.client.get(reverse("jobs:job_alerts"), {"created": alert.pk})
        self.assertContains(page, "Was this a useful match?")
        self.assertContains(page, "scout-feedback-button is-selected")

    def test_user_cannot_rate_another_users_delivery(self):
        other = User.objects.create_user(
            username="other-scout", email="other-scout@example.com", password="test-pass-123"
        )
        alert = JobAlert.objects.create(user=other, name="Private scout")
        delivery = JobAlertDelivery.objects.create(alert=alert, job=self.job)

        response = self.client.post(
            reverse("jobs:job_alert_feedback", args=[delivery.pk]),
            {"feedback": JobAlertDelivery.Feedback.NOT_RELEVANT},
        )

        self.assertEqual(response.status_code, 404)
        delivery.refresh_from_db()
        self.assertEqual(delivery.feedback, "")

    @patch.object(email_service, "send_email", return_value=True)
    def test_one_time_email_has_scoped_unsubscribe_and_upgrade(self, send_email):
        alert = JobAlert.objects.create(
            user=self.user,
            name="Engineering",
            intent_text="Remote software engineering roles using Python.",
            frequency=JobAlert.Frequency.ONCE,
            is_active=False,
        )
        self.job.scout_score = 88
        self.job.scout_reasons = ["Role title closely matches what you described"]

        sent = email_service.send_job_alert(alert, [self.job], is_initial=True)

        self.assertTrue(sent)
        kwargs = send_email.call_args.kwargs
        self.assertIn("Keep My Scout Running", kwargs["html"])
        self.assertIn("source=free_scout_email", kwargs["html"])
        self.assertIn("Rate these matches", kwargs["html"])
        self.assertIn(
            f"?created={alert.pk}&amp;source=scout_email_feedback", kwargs["html"]
        )
        self.assertIn("/alerts/unsubscribe/", kwargs["html"])
        self.assertNotIn("/newsletter/unsubscribe/", kwargs["html"])
        self.assertIn(
            f"?source=scout_email&amp;alert_id={alert.pk}", kwargs["html"]
        )
        self.assertIn(
            f"?source=scout_email&alert_id={alert.pk}", kwargs["text"]
        )

    def test_scout_email_job_visit_is_recorded(self):
        alert = JobAlert.objects.create(
            user=self.user,
            name="Engineering",
            intent_text="Remote software engineering roles using Python.",
        )

        response = self.client.get(
            self.job.get_absolute_url(),
            {"source": "scout_email", "alert_id": alert.pk},
        )

        self.assertEqual(response.status_code, 200)
        event = ConsumerFunnelEvent.objects.filter(
            user=self.user,
            alert=alert,
            event=ConsumerFunnelEvent.Event.SCOUT_JOB_VIEWED,
        ).latest("created_at")
        self.assertEqual(event.metadata["source"], "scout_email")
        self.assertEqual(event.metadata["job_id"], self.job.pk)


class ProCheckoutTest(TestCase):
    @override_settings(STRIPE_SECRET_KEY="sk_test_example")
    @patch("jobs.services.payment_service.stripe.checkout.Session.create")
    @patch("jobs.services.payment_service.stripe.Customer.create")
    def test_checkout_is_ten_dollars_and_bundled(self, create_customer, create_session):
        user = User.objects.create_user(
            username="payer", email="payer@example.com", password="test-pass-123"
        )
        create_customer.return_value = Mock(id="cus_123")
        create_session.return_value = Mock(
            id="cs_123",
            url="https://checkout.example/session",
            expires_at=int((timezone.now() + timedelta(days=1)).timestamp()),
        )

        url = PaymentService.create_pro_subscription_session(
            user,
            "https://remoteimpact.org",
            success_path="/alerts/pro/success/",
            cancel_path="/alerts/",
            source="free_scout_results",
        )

        self.assertEqual(url, "https://checkout.example/session")
        kwargs = create_session.call_args.kwargs
        price_data = kwargs["line_items"][0]["price_data"]
        self.assertEqual(price_data["unit_amount"], 1000)
        self.assertEqual(price_data["product_data"]["name"], "Remote Impact Pro")
        self.assertEqual(
            kwargs["subscription_data"]["metadata"]["user_id"], str(user.id)
        )
        self.assertEqual(kwargs["metadata"]["source"], "free_scout_results")
        self.assertEqual(kwargs["client_reference_id"], str(user.id))
        self.assertTrue(kwargs["after_expiration"]["recovery"]["enabled"])
        subscription = AssistantSubscription.objects.get(user=user)
        self.assertEqual(subscription.stripe_checkout_session_id, "cs_123")

    @override_settings(STRIPE_SECRET_KEY="sk_test_example")
    @patch("jobs.services.payment_service.stripe.checkout.Session.create")
    @patch("jobs.services.payment_service.stripe.checkout.Session.retrieve")
    def test_checkout_reuses_an_open_session(self, retrieve, create):
        user = User.objects.create_user(
            username="returning-payer",
            email="returning@example.com",
            password="test-pass-123",
        )
        AssistantSubscription.objects.create(
            user=user,
            stripe_customer_id="cus_existing",
            stripe_checkout_session_id="cs_open",
        )
        retrieve.return_value = Mock(
            status="open", url="https://checkout.example/existing"
        )

        url = PaymentService.create_pro_subscription_session(
            user, "https://remoteimpact.org"
        )

        self.assertEqual(url, "https://checkout.example/existing")
        create.assert_not_called()

    def test_checkout_webhook_keeps_pro_active_with_fallback_expiry(self):
        user = User.objects.create_user(
            username="webhook-payer",
            email="webhook-payer@example.com",
            password="test-pass-123",
        )
        subscription = AssistantSubscription.get_or_create_for_user(user)
        event = {
            "id": "evt_checkout_completed",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_checkout_completed",
                    "mode": "subscription",
                    "subscription": "sub_checkout_completed",
                    "customer": "cus_checkout_completed",
                    "metadata": {
                        "user_id": str(user.id),
                        "source": "free_scout_results",
                    },
                }
            },
        }

        PaymentService.handle_webhook_event(event)

        subscription.refresh_from_db()
        self.assertTrue(subscription.is_pro_active())
        self.assertIsNotNone(subscription.expires_at)
        self.assertGreater(subscription.expires_at, timezone.now())
        payment_event = ConsumerFunnelEvent.objects.get(
            user=user, event=ConsumerFunnelEvent.Event.PAYMENT_COMPLETED
        )
        self.assertEqual(payment_event.metadata["source"], "free_scout_results")

    @patch(
        "jobs.views.job_alerts.PaymentService.create_pro_subscription_session",
        return_value=Mock(id="cs_confirmed", url="https://checkout.example/session"),
    )
    def test_checkout_records_server_side_funnel_events(self, create_session):
        user = User.objects.create_user(
            username="checkout", email="checkout@example.com", password="test-pass-123"
        )
        self.client.force_login(user)

        confirmation = self.client.get(
            reverse("jobs:job_scout_subscribe"), {"source": "free_scout_results"}
        )
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Start 7-day free trial")
        self.assertContains(confirmation, "Get the 3-month pass")

        response = self.client.post(
            reverse("jobs:job_scout_subscribe"),
            {"source": "free_scout_results"},
        )
        self.assertRedirects(
            response, "https://checkout.example/session", fetch_redirect_response=False
        )
        self.assertTrue(create_session.call_args.kwargs["return_session"])
        events = list(
            ConsumerFunnelEvent.objects.filter(user=user).order_by("created_at", "pk").values_list(
                "event", flat=True
            )
        )
        self.assertEqual(
            events,
            [
                ConsumerFunnelEvent.Event.ACCOUNT_CREATED,
                ConsumerFunnelEvent.Event.UPGRADE_CLICKED,
                ConsumerFunnelEvent.Event.CHECKOUT_CREATED,
            ],
        )

    def test_expired_checkout_is_recorded_and_cached_session_is_cleared(self):
        user = User.objects.create_user(
            username="expired-payer",
            email="expired@example.com",
            password="test-pass-123",
        )
        subscription = AssistantSubscription.objects.create(
            user=user,
            stripe_checkout_session_id="cs_expired",
            stripe_checkout_expires_at=timezone.now(),
        )

        PaymentService.handle_webhook_event(
            {
                "id": "evt_expired",
                "type": "checkout.session.expired",
                "data": {
                    "object": {
                        "id": "cs_expired",
                        "metadata": {
                            "user_id": str(user.id),
                            "source": "free_scout_results",
                        },
                    }
                },
            }
        )

        subscription.refresh_from_db()
        self.assertEqual(subscription.stripe_checkout_session_id, "")
        event = ConsumerFunnelEvent.objects.get(
            user=user, event=ConsumerFunnelEvent.Event.CHECKOUT_EXPIRED
        )
        self.assertEqual(event.metadata["checkout_session_id"], "cs_expired")

    @override_settings(STRIPE_SECRET_KEY="sk_test_example")
    @patch("jobs.services.payment_service.stripe.checkout.Session.retrieve")
    def test_paid_checkout_activates_pro_and_records_payment_once(self, retrieve):
        user = User.objects.create_user(
            username="verified-payer",
            email="verified-payer@example.com",
            password="test-pass-123",
        )
        retrieve.return_value = Mock(
            payment_status="paid",
            subscription="sub_123",
            metadata={"user_id": str(user.id)},
        )

        first, subscription = PaymentService.verify_assistant_subscription(
            "cs_123", user
        )
        second, _ = PaymentService.verify_assistant_subscription("cs_123", user)

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertTrue(subscription.is_pro_active())
        self.assertEqual(subscription.stripe_subscription_id, "sub_123")
        self.assertEqual(
            ConsumerFunnelEvent.objects.filter(
                user=user,
                event=ConsumerFunnelEvent.Event.PAYMENT_COMPLETED,
            ).count(),
            1,
        )

    def test_subscription_webhook_cancels_pro_and_records_event_once(self):
        user = User.objects.create_user(
            username="cancelled-payer",
            email="cancelled-payer@example.com",
            password="test-pass-123",
        )
        subscription = AssistantSubscription.get_or_create_for_user(user)
        subscription.is_subscribed = True
        subscription.stripe_subscription_id = "sub_cancelled"
        subscription.expires_at = timezone.now() + timedelta(days=20)
        subscription.save()
        event = {
            "id": "evt_cancelled",
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_cancelled"}},
        }

        PaymentService.handle_webhook_event(event)
        PaymentService.handle_webhook_event(event)

        subscription.refresh_from_db()
        self.assertFalse(subscription.is_subscribed)
        self.assertEqual(
            ConsumerFunnelEvent.objects.filter(
                user=user,
                event=ConsumerFunnelEvent.Event.SUBSCRIPTION_CANCELLED,
            ).count(),
            1,
        )

    def test_subscription_webhook_activates_and_extends_pro_period(self):
        user = User.objects.create_user(
            username="renewing-payer",
            email="renewing-payer@example.com",
            password="test-pass-123",
        )
        subscription = AssistantSubscription.get_or_create_for_user(user)
        period_end = int((timezone.now() + timedelta(days=31)).timestamp())
        event = {
            "id": "evt_renewed",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_renewing",
                    "customer": "cus_renewing",
                    "status": "active",
                    "metadata": {"user_id": str(user.id)},
                    "items": {
                        "data": [{"current_period_end": period_end}],
                    },
                }
            },
        }

        PaymentService.handle_webhook_event(event)

        subscription.refresh_from_db()
        self.assertTrue(subscription.is_pro_active())
        self.assertEqual(subscription.stripe_subscription_id, "sub_renewing")
        self.assertEqual(subscription.stripe_customer_id, "cus_renewing")
        self.assertEqual(int(subscription.expires_at.timestamp()), period_end)

    @override_settings(STRIPE_SECRET_KEY="sk_test_example")
    @patch("jobs.services.payment_service.stripe.billing_portal.Session.create")
    @patch("jobs.services.payment_service.stripe.billing_portal.Configuration.list")
    def test_billing_portal_uses_an_active_configuration(self, list_configs, create):
        user = User.objects.create_user(
            username="billing-payer",
            email="billing-payer@example.com",
            password="test-pass-123",
        )
        subscription = AssistantSubscription.get_or_create_for_user(user)
        subscription.stripe_customer_id = "cus_billing"
        subscription.save(update_fields=["stripe_customer_id"])
        list_configs.return_value = Mock(data=[Mock(id="bpc_live")])
        create.return_value = Mock(url="https://billing.stripe.com/session")

        url = PaymentService.create_billing_portal_session(
            user, "https://remoteimpact.org"
        )

        self.assertEqual(url, "https://billing.stripe.com/session")
        create.assert_called_once_with(
            customer="cus_billing",
            return_url="https://remoteimpact.org/account/",
            configuration="bpc_live",
        )
