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
