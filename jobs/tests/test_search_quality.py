from django.test import TestCase

from jobs.models import Job, Organization
from jobs.services.job_service import JobService


class SearchQualityTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Ocean Futures", slug="ocean-futures")
        other = Organization.objects.create(name="Plain Org", slug="plain-org")
        for title, organization in [
            ("AI Policy Lead", other), ("Maintenance Coordinator", other), ("Software Engineers", other),
            ("Communications Internship", other), ("International Program Officer", other),
            ("Marketing Manager", other), ("Data Analyst", org),
        ]:
            Job.objects.create(title=title, slug=title.lower().replace(" ", "-"), organization=organization, description="x")

    def titles(self, query):
        return set(JobService.get_filtered_jobs({"q": query}).values_list("title", flat=True))

    def test_short_words_match_whole_words_only(self):
        self.assertIn("AI Policy Lead", self.titles("AI policy"))
        self.assertNotIn("Maintenance Coordinator", self.titles("AI policy"))

    def test_intern_finds_internships_not_international(self):
        found = self.titles("intern comms")
        self.assertIn("Communications Internship", found)
        self.assertNotIn("International Program Officer", found)

    def test_filler_words_are_ignored_and_plurals_match(self):
        self.assertEqual(self.titles("remote marketing jobs"), {"Marketing Manager"})
        self.assertIn("Software Engineers", self.titles("software engineer"))

    def test_organization_name_is_searchable(self):
        self.assertIn("Data Analyst", self.titles("Ocean Futures"))


class CountryCorrectionTests(TestCase):
    def test_false_worldwide_is_corrected_only_with_evidence(self):
        from io import StringIO
        from django.core.management import call_command
        org = Organization.objects.create(name="Geo Org", slug="geo-org")
        cases = {
            "London, England, GB": ("", "UK"),
            "Zürich, ZH, CH": ("", "Switzerland"),
            "Remote, CA": ("", "Worldwide"),
            "Remote": ("Applicants must be based in the United States.", "USA"),
            "Remote ": ("Work from anywhere.", "Worldwide"),
            "Remote, Global": ("", "Worldwide"),
        }
        jobs = {}
        for i, (location, (body, _)) in enumerate(cases.items()):
            jobs[location] = Job.objects.create(
                title=f"Role {i}", slug=f"geo-role-{i}", organization=org, location=location,
                country="Worldwide", description=body or "x",
            )
        call_command("fix_job_countries", stdout=StringIO())
        self.assertEqual(Job.objects.get(pk=jobs["London, England, GB"].pk).country, "Worldwide")  # dry run
        call_command("fix_job_countries", "--apply", stdout=StringIO())
        for location, (_, expected) in cases.items():
            job = Job.objects.get(pk=jobs[location].pk)
            self.assertEqual(job.country, expected, location)
        self.assertEqual(Job.objects.get(pk=jobs["London, England, GB"].pk).raw_data["country_correction"]["from"], "Worldwide")


class EmptyScoutAdviceTests(TestCase):
    def test_advice_names_the_dominant_blocker(self):
        from jobs.services.job_scout_service import JobScoutService
        advice = JobScoutService.empty_advice({"hard_rejections": {"location": 900, "salary": 12}}, {"locations": ["Kenya"]})
        self.assertIn("Kenya", advice)
        self.assertIn("legally work", advice)
        self.assertIn("salary floor", JobScoutService.empty_advice({"hard_rejections": {"salary": 50}}, {}))
        self.assertIn("related job titles", JobScoutService.empty_advice({"title_rejected": 40}, {}))
        self.assertTrue(JobScoutService.empty_advice({}, {}))


class ScoutTitleIntentTests(TestCase):
    def matches(self, title, desired, context=""):
        from jobs.services.job_scout_service import JobScoutService
        return JobScoutService._title_matches_intent(title, desired, context)

    def test_employment_qualifiers_do_not_block_titles(self):
        desired = ["Manager of Program Operations (Part Time Contractor)", "Program Operations Manager (Part-Time Contractor)"]
        self.assertTrue(self.matches("Program Operations Manager", desired))

    def test_c_suite_abbreviations_expand(self):
        self.assertTrue(self.matches("Chief Operating Officer", ["COO"]))
        self.assertFalse(self.matches("Chief Financial Officer", ["COO"]))

    def test_product_designer_accepts_ux_designer_on_product_work_only(self):
        product_context = "You will own product design for our mobile app and run user research."
        self.assertTrue(self.matches("Senior UX Designer", ["Senior Product Designer"], product_context))
        self.assertFalse(self.matches("Graphic Designer", ["Senior Product Designer"], "Design print materials and event signage."))
        self.assertFalse(self.matches("Product Manager", ["Senior Product Designer"], product_context))


class EarlyCareerSeniorityTests(TestCase):
    def test_students_are_read_as_early_career(self):
        from jobs.services.job_scout_service import JobScoutService
        parsed = JobScoutService._fallback_parse(
            "I am looking for a job in IT, I currently have 3 semesters left of school before I graduate.")
        self.assertEqual(parsed["experience_levels"], ["internship", "entry"])

    def test_untagged_senior_titles_are_not_sent_to_students(self):
        from jobs.services.job_scout_service import JobScoutService
        self.assertTrue(JobScoutService.SENIOR_TITLE.search("Senior Security Engineer - SecOps"))
        self.assertTrue(JobScoutService.SENIOR_TITLE.search("Information Security Lead"))
        self.assertFalse(JobScoutService.SENIOR_TITLE.search("IT Support Associate"))
        self.assertFalse(JobScoutService.SENIOR_TITLE.search("Leadership Programs Coordinator"))


class OrganizationInterleavingTests(TestCase):
    def test_one_employer_does_not_fill_the_top_of_a_day(self):
        from datetime import timedelta
        from django.utils import timezone
        big = Organization.objects.create(name="Big Corp", slug="big-corp")
        small = Organization.objects.create(name="Small NGO", slug="small-ngo")
        now = timezone.now()
        for i in range(5):
            Job.objects.create(title=f"Big {i}", slug=f"big-{i}", organization=big, description="x",
                               posted_at=now - timedelta(minutes=i))
        Job.objects.create(title="Small role", slug="small-role", organization=small, description="x",
                           posted_at=now - timedelta(minutes=30))
        Job.objects.create(title="Yesterday", slug="yesterday", organization=small, description="x",
                           posted_at=now - timedelta(days=1))
        titles = list(JobService.get_filtered_jobs({}).values_list("title", flat=True))
        self.assertEqual(titles[:2], ["Big 0", "Small role"])
        self.assertEqual(titles[-1], "Yesterday")


class SingleWordPrecisionTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Grid Co", slug="grid-co")
        for i in range(10):
            Job.objects.create(title=f"Communications Manager {i}", slug=f"comms-{i}", organization=org,
                               description="Lead our storytelling.")
        Job.objects.create(title="Field Technician", slug="field-tech", organization=org,
                           description="Good communications skills and operations experience required.")
        Job.objects.create(title="Data Engineer", slug="data-eng", organization=org, description="Build pipelines.",
                           skills=["python", "sql"])

    def titles(self, q):
        return set(JobService.get_filtered_jobs({"q": q}).values_list("title", flat=True))

    def test_body_only_mentions_drop_out_when_there_are_enough_real_matches(self):
        found = self.titles("communications")
        self.assertNotIn("Field Technician", found)
        self.assertEqual(len(found), 10)

    def test_skills_count_as_a_real_match_and_body_is_a_fallback(self):
        self.assertIn("Data Engineer", self.titles("python"))
        self.assertIn("Field Technician", self.titles("operations"))


class SoftSkillSearchTests(TestCase):
    def test_soft_skill_tags_do_not_count_as_a_match(self):
        org = Organization.objects.create(name="Soft Org", slug="soft-org")
        for i in range(10):
            Job.objects.create(title=f"Communications Lead {i}", slug=f"comms-lead-{i}", organization=org, description="x")
        Job.objects.create(title="Grid Engineer", slug="grid-eng", organization=org, description="x",
                           skills=["communication", "power-systems"])
        found = set(JobService.get_filtered_jobs({"q": "communications"}).values_list("title", flat=True))
        self.assertNotIn("Grid Engineer", found)


class GenericListingTests(TestCase):
    def test_open_applications_in_other_languages_are_not_openings(self):
        from jobs.services.importers.common import _is_generic_listing_title
        for title in ["Initiativbewerbung (m/w/d)", "Initiativbewerbung (all genders) - Gestalte mit uns die Energiewende",
                      "Can’t Find the Right Role? Join Our Talent Network", "Candidature spontanée"]:
            self.assertTrue(_is_generic_listing_title(title), title)
        self.assertFalse(_is_generic_listing_title("Network Engineer"))


class LocationVariantTests(TestCase):
    def test_same_role_posted_many_times_is_listed_once(self):
        org = Organization.objects.create(name="Writer Org", slug="writer-org")
        for i in range(3):
            Job.objects.create(title="Grant Writer", slug=f"gw-{i}", organization=org, description="x")
        Job.objects.create(title="Program Officer", slug="po", organization=org, description="x")
        titles = list(JobService.get_filtered_jobs({}).values_list("title", flat=True))
        self.assertEqual(sorted(titles), ["Grant Writer", "Program Officer"])
        self.assertEqual(JobService.get_filtered_jobs({"q": "grant writer"}).count(), 1)


class RoleSearchBroadeningTests(TestCase):
    def test_rare_role_title_falls_back_to_subject_word(self):
        org = Organization.objects.create(name="Policy Org", slug="policy-org")
        for title in ["Associate Policy Analyst", "Senior Policy Advisor", "Policy Manager", "Sales Manager"]:
            Job.objects.create(title=title, slug=title.lower().replace(" ", "-"), organization=org, description="x")
        found = set(JobService.get_filtered_jobs({"q": "policy analyst"}).values_list("title", flat=True))
        self.assertEqual(found, {"Associate Policy Analyst", "Senior Policy Advisor", "Policy Manager"})


class ListingLanguageTests(TestCase):
    def test_detects_german_and_french_but_not_english(self):
        from jobs.services.listing_language import listing_language
        self.assertEqual(listing_language("Projektleitung (m/w/d)", "Wir suchen dich für unser Team und die Energiewende mit uns."), "German")
        self.assertEqual(listing_language("Chargé de projet", "Nous recherchons une personne pour rejoindre notre équipe avec des compétences dans les projets et des partenaires."), "French")
        self.assertEqual(listing_language("Program Manager", "You will lead our programs and work with the team on climate projects for our partners."), "")
