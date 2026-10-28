from django.test import SimpleTestCase
from jobs.models import Job


class EntryBadgeTests(SimpleTestCase):
    def test_explicit_seniority_overrides_ambiguous_title(self):
        for title in ['Assistant General Counsel, Transactions', 'Associate Asset Manager', 'Senior Research Assistant']:
            with self.subTest(title=title):
                self.assertFalse(Job(title=title, experience_level='mid').is_entry_level_friendly)

    def test_known_entry_level_kept(self):
        self.assertTrue(Job(title='Programme Assistant', experience_level='entry').is_entry_level_friendly)

    def test_substring_is_not_entry_signal(self):
        self.assertFalse(Job(title='International Relations Director', experience_level='').is_entry_level_friendly)

    def test_clear_unknown_level_titles(self):
        for title in ['Junior Analyst', 'Research Internship', 'Entry-level Engineer', 'Trainee']:
            with self.subTest(title=title):
                self.assertTrue(Job(title=title, experience_level='').is_entry_level_friendly)
        for title in ['Senior Research Assistant', 'Associate Director', 'Assistant General Counsel', 'Programme Coordinator']:
            with self.subTest(title=title):
                self.assertFalse(Job(title=title, experience_level='').is_entry_level_friendly)

    def test_explicit_senior_level_wins_even_over_junior_word(self):
        self.assertFalse(Job(title='Junior Programme Director', experience_level='senior').is_entry_level_friendly)
