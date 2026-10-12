"""Brief phrasings and job facts that used to slip past Scout's hard rules (found by eval_ranking)."""
from types import SimpleNamespace

from django.test import SimpleTestCase

from jobs.services.job_scout_service import JobScoutService
from jobs.services.scout_constraints import extract_constraints
from jobs.services.scout_evidence import experience_conflict


class BriefPhrasings(SimpleTestCase):
    def test_fractional_interim_project_based_mean_part_time_or_contract(self):
        for brief in ("a remote fractional or interim executive role", "project-based advisory work"):
            self.assertEqual(set(JobScoutService._explicit_job_types(brief.lower())), {"part-time", "contract"})

    def test_hours_per_week_under_thirty_means_part_time(self):
        self.assertIn("part-time", JobScoutService._explicit_job_types("roughly 15–25 hours/week, flexible"))
        self.assertNotIn("part-time", JobScoutService._explicit_job_types("happy to work 40 hours per week"))

    def test_domain_words_are_not_job_types(self):
        self.assertEqual(JobScoutService._explicit_job_types("interim reports for global health programs".replace("interim", "quarterly")), [])

    def test_pay_floor_in_words(self):
        self.assertEqual(extract_constraints("ethics of ai from 80 thousand a year salary")["min_salary"], 80000)
        hourly = extract_constraints("AI training work, around $40/hour paid weekly")["salary_requirement"]
        self.assertEqual((hourly["amount"], hourly["period"]), ("40", "hour"))

    def test_bare_numbers_are_not_pay(self):
        self.assertIsNone(extract_constraints("I have 12 years in policy and 3 kids")["salary_requirement"])


class EntryLevelYears(SimpleTestCase):
    def job(self, title="Research Analyst"):
        return SimpleNamespace(title=title)

    def test_two_plus_years_is_beyond_entry_level(self):
        prefs = {"experience_levels": ["entry"]}
        self.assertTrue(experience_conflict(self.job(), prefs, "You have 2+ years of research experience."))
        self.assertTrue(experience_conflict(self.job(), prefs, "At least 2 years of relevant experience."))
        self.assertFalse(experience_conflict(self.job(), prefs, "1-2 years of experience is welcome."))
