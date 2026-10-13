from django.test import SimpleTestCase
from jobs.services.seo_text import TITLE_LIMIT, job_title_tag


class SeoTitleIdentityTests(SimpleTestCase):
    def test_region_qualifiers_survive_long_employer_and_role_names(self):
        employer='Intertribal Agriculture Council'
        titles=[job_title_tag('Regional Outreach and Education Manager- '+region+' Region',employer) for region in ['NW','SW']]
        self.assertNotEqual(*titles)
        for title,region in zip(titles,['NW','SW']):
            self.assertIn(region+' Region',title)
            self.assertTrue(title.startswith('Regional'))
            self.assertLessEqual(len(title),TITLE_LIMIT)

    def test_specializations_are_not_trimmed_to_identical_titles(self):
        roles=['Technical Business Development Strategist – '+specialty for specialty in ['Distribution','EMA','Transmission Planning']]
        titles=[job_title_tag(role,'Electric Power Engineers') for role in roles]
        self.assertEqual(len(set(titles)),3)
        for title in titles:self.assertLessEqual(len(title),TITLE_LIMIT)


class EmployerRepetitionTests(SimpleTestCase):
    def test_remove_only_trailing_exact_employer_attribution(self):
        title = job_title_tag("Communications and Media Relations Manager for World BEYOND War", "World BEYOND War")
        self.assertEqual(title.count("World BEYOND War"), 1)
        self.assertIn("Communications", title)
        self.assertEqual(job_title_tag("Analyst at RAND", "RAND"), job_title_tag("Analyst", "RAND"))

    def test_preserve_role_words_and_nonmatching_organizations(self):
        self.assertIn("Advocate for Children", job_title_tag("Advocate for Children", "Acme"))
        self.assertIn("RAND Policy Analyst", job_title_tag("RAND Policy Analyst", "RAND"))
        self.assertIn("Analyst for RAND Europe", job_title_tag("Analyst for RAND Europe", "RAND"))
