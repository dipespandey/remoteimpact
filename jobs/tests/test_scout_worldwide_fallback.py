"""Country-specific Scouts can use global openings without inventing eligibility."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from jobs.services.scout_remote_policy import check_matches, requested_candidate
from jobs.services.scout_constraints import location_compatibility, extract_constraints
from jobs.services.scout_tiers import tier_for_job
from ml.remote_policy.core import worldwide_permission, rules, Candidate


def listing(pk=1, location='Remote, Global', description='Conduct research and write reports.'):
    return SimpleNamespace(pk=pk, title='Research Assistant', location=location, country='',
        description=description, requirements='', raw_data={}, scout_caveats=[], scout_reasons=[])


@override_settings(SCOUT_REMOTE_POLICY_ENABLED=True, DEEPSEEK_API_KEY='')
class WorldwideFallbackTests(SimpleTestCase):
    prefs = {'locations':['Pakistan']}

    def test_exact_global_remote_labels_are_recognized_without_inference(self):
        for label in ['Remote, Global','Remote (Worldwide)','Remote — Anywhere','Remote worldwide']:
            with self.subTest(label=label):
                self.assertTrue(worldwide_permission(label))
                self.assertEqual(rules('', Candidate('PK'), location=label).label, 'compatible')
        for label in ['Remote', 'Global health', 'Remote, US only', 'Remote team with global customers']:
            self.assertFalse(worldwide_permission(label))

    def test_worldwide_is_a_caveated_possibility_for_a_country_request(self):
        item = listing()
        result = check_matches([item], self.prefs, {}, allow_api=False)
        self.assertEqual(result, [item])
        self.assertEqual(item.scout_location_priority, 1)
        self.assertIn('worldwide_location_fallback', item.scout_caveats)
        self.assertEqual(tier_for_job(item), 'consider')

    def test_specific_country_precedes_worldwide_and_unknown(self):
        country, global_job, unknown = listing(1,'Remote Pakistan'), listing(2), listing(3,'Remote')
        result = check_matches([unknown, global_job, country], self.prefs, {}, allow_api=False)
        self.assertEqual([j.scout_location_priority for j in result], [2,1,0])
        self.assertNotIn('worldwide_location_fallback', country.scout_caveats)

    def test_global_does_not_override_an_exclusion_or_us_only_policy(self):
        for item in [listing(location='Remote US only'),
                     listing(description='Remote worldwide except Pakistan.'),
                     listing(description='Candidates must be based in the United States.')]:
            self.assertEqual(check_matches([item], self.prefs, {}, allow_api=False), [])

    def test_global_with_unresolved_hours_remains_possible(self):
        item = listing(description='Remote worldwide. Must overlap US working hours.')
        self.assertEqual(check_matches([item], self.prefs, {}, allow_api=False), [item])
        self.assertEqual(item.scout_remote_policy['label'], 'unknown')
        self.assertEqual(tier_for_job(item), 'consider')

    def test_generic_remote_with_default_worldwide_country_is_not_explicit_global(self):
        item = listing(location='Remote'); item.country='Worldwide'
        check_matches([item], self.prefs, {}, allow_api=False)
        self.assertEqual(item.scout_location_priority, 2)
        self.assertNotIn('worldwide_location_fallback', item.scout_caveats)

    def test_worldwide_including_home_country_preserves_home_country_check(self):
        self.assertEqual(requested_candidate({'locations':['Pakistan','Worldwide']}).country, 'PK')
        self.assertTrue(location_compatibility(listing(location='Remote Pakistan'), ['Pakistan','Worldwide']))
        self.assertFalse(location_compatibility(listing(location='Remote US only'), ['Pakistan','Worldwide']))
        self.assertTrue(location_compatibility(listing(), ['Pakistan','Worldwide']))
        self.assertIsNone(requested_candidate({'locations':['Worldwide']}))
        self.assertIsNone(requested_candidate({'locations':['Pakistan','Europe']}))


@override_settings(SCOUT_REMOTE_POLICY_ENABLED=True,DEEPSEEK_API_KEY='',SCOUT_RERANK_ENABLED=False,
                   SCOUT_MULTISTEP_ENABLED=False,SCOUT_CLOSE_MATCHES=False)
class WorldwideMatchPipelineTests(TestCase):
    def test_global_fills_empty_country_search_and_specific_matches_rank_first(self):
        from jobs.models import Organization,Job
        from jobs.services.job_scout_service import JobScoutService as Scout
        org=Organization.objects.create(name='Research Group')
        def make(slug, location):
            return Job.objects.create(slug=slug, title='Research Assistant', organization=org,
                description='Conduct research and write reports.', location=location, country='',
                requirements='One year of experience.', experience_level='entry',
                posted_at=timezone.now())
        global_job=make('global-research','Remote, Global')
        us_only=make('us-research','Remote US only')
        prefs=Scout._fallback_parse('Remote Research Assistant roles from Pakistan, entry-level.')
        matches=Scout.get_matches_for_preferences(prefs,limit=3)
        self.assertIn(global_job.pk,[j.pk for j in matches])
        self.assertNotIn(us_only.pk,[j.pk for j in matches])
        match=next(j for j in matches if j.pk==global_job.pk)
        self.assertIn('Worldwide role', ' '.join(match.scout_reasons))
        self.assertEqual(tier_for_job(match),'consider')
        country=make('pakistan-research','Remote Pakistan')
        matches=Scout.get_matches_for_preferences(prefs,limit=1)
        self.assertEqual([j.pk for j in matches],[country.pk])
