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
        self.assertIn("worldwide", advice)
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
