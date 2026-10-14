import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from jobs.services.scout_review import evidence_fields, workplace_conflict, review_checks, apply_review, listing_caveats
from jobs.services.scout_reranker import rerank
from jobs.services.scout_close_matches import validate
from jobs.services.scout_tiers import tier_for_job
from jobs.tests.test_scout_pipeline import job


class SourceReviewTests(SimpleTestCase):
    def test_late_duties_and_residence_replace_duplicated_company_intro(self):
        text = 'We are an innovative climate company. ' * 80 + (
            'Responsibilities: own customer success and upsell enterprise SaaS. '
            'This position requires you to be located in Germany. '
            'Qualifications: four years of policy experience. Salary undisclosed.')
        row = evidence_fields(job(description=text, requirements=text), {'intent_text': 'climate policy in Europe'})
        self.assertEqual(row['requirements'], '')
        self.assertIn('own customer success and upsell enterprise SaaS', row['description'])
        self.assertIn('located in Germany', row['description'])
        self.assertLessEqual(len(row['description']), 2800)

    def test_explicit_hybrid_is_not_remote_but_remote_computing_is_not_workplace(self):
        prefs = {'intent_text': 'Remote Python engineering in Europe'}
        for text in ['This role offers a hybrid working model, combining remote work with collaboration from our office in Plovdiv.',
                     'This is an on-site position.', 'You must work from our office.']:
            self.assertTrue(workplace_conflict(job(), prefs, text))
        for text in ['Build hybrid cloud systems and remote build caches.',
                     'Hybrid working is optional; you may work fully remotely.', 'Remote and hybrid work optionsTeam retreats and geographic meetups.']:
            self.assertFalse(workplace_conflict(job(), prefs, text))
        self.assertFalse(workplace_conflict(job(), {'intent_text': 'Remote or hybrid Python roles'}, 'Hybrid working model.'))

    def test_title_or_mission_does_not_substitute_for_duties_check(self):
        row = {'title': 'Climate Strategist', 'description': 'Own customer success and upsell enterprise SaaS.'}
        item = {'checks': {'function': ['confirmed', 'title', 'Climate Strategist']}}
        self.assertIsNone(review_checks(item, row, {}))
        item['checks']['function'] = ['conflicting', 'description', row['description']]
        self.assertIsNone(review_checks(item, row, {}))

    def test_missing_forged_and_conflicting_checks_fail_closed(self):
        row = {'description': 'Develop industrial policy recommendations.'}
        prefs = {'locations': ['Europe']}
        item = {'checks': {'function': ['confirmed', 'description', row['description']]}}
        self.assertIsNone(review_checks(item, row, prefs))
        item['checks']['location'] = ['confirmed', 'description', 'Work anywhere throughout Europe']
        self.assertIsNone(review_checks(item, row, prefs))
        item['checks']['location'] = ['conflicting', 'description', row['description']]
        self.assertIsNone(review_checks(item, row, prefs))

    def test_policy_role_with_unknown_pay_is_consider_and_residence_is_visible(self):
        row = {'description': 'Develop industrial policy recommendations. You must be located in Germany.'}
        prefs = {'locations': ['Europe'], 'min_salary': 70000}
        item = {'checks': {'function': ['confirmed', 'description', 'Develop industrial policy recommendations'],
                           'location': ['confirmed', 'description', 'You must be located in Germany'],
                           'salary': ['unknown', '', '']}}
        j = job(description=row['description'],requirements='')
        self.assertTrue(apply_review(j, item, row, prefs))
        self.assertEqual(tier_for_job(j), 'consider')
        self.assertIn('Location evidence: You must be located in Germany', j.scout_reasons)
        self.assertIn('review_salary', j.scout_caveats)

    def test_close_match_requires_checks_even_with_same_function_boolean(self):
        row = {'description': 'Own customer success and upsell enterprise SaaS.'}
        item = {'id': 1, 'same_kind_of_work': True, 'difference': 'Climate strategy instead of climate policy',
                'evidence': {'field': 'description', 'quote': row['description']}}
        self.assertEqual(validate(json.dumps({'picks': [item]}), {1: row}, 3, {}), [])
        item['checks'] = {'function': ['conflicting', 'description', row['description']]}
        self.assertEqual(validate(json.dumps({'picks': [item]}), {1: row}, 3, {}), [])

    def test_office_address_and_remote_setting_are_not_verified_eligibility(self):
        row = {'description': 'Build Python services in a remote setting.', 'location': 'Zürich, CH'}
        prefs = {'intent_text': 'Remote Python roles', 'locations': ['Europe']}
        item = {'checks': {'function': ['confirmed', 'description', 'Build Python services'],
                          'remote': ['confirmed', 'description', 'in a remote setting'],
                          'location': ['confirmed', 'location', 'Zürich, CH']}}
        self.assertEqual(review_checks(item, row, prefs), ['review_remote', 'review_location'])

    def test_short_remote_location_label_does_not_drop_relevant_job(self):
        row = {'description': 'Develop climate policy recommendations.', 'location': 'Remote'}
        item = {'checks': {'function': ['confirmed', 'description', row['description']],
                          'remote': ['confirmed', 'location', 'Remote']}}
        self.assertEqual(review_checks(item, row, {'intent_text': 'Remote policy roles'}), [])

    def test_sector_advisory_quote_cannot_confirm_policy_function(self):
        quote = 'We offer customers a sustainability management platform to measure emissions.'
        row = {'description': quote}
        item = {'checks': {'function': ['confirmed', 'description', quote]}}
        self.assertIsNone(review_checks(item, row, {'intent_text': 'Climate policy roles'}))

    def test_recent_feed_sighting_does_not_refresh_old_posting(self):
        j = SimpleNamespace(posted_at=timezone.now()-timedelta(days=730), last_seen_at=timezone.now())
        self.assertEqual(listing_caveats(j), ['listing_age'])
        j.posted_at = timezone.now()-timedelta(days=7)
        self.assertEqual(listing_caveats(j), [])

    @override_settings(SCOUT_RERANK_ENABLED=True, DEEPSEEK_API_KEY='test', SCOUT_RERANK_BACKEND='deepseek')
    @patch('jobs.services.scout_reranker.cache')
    @patch('jobs.services.scout_workflow.generate')
    def test_provider_failure_does_not_promote_unreviewed_results(self, generate, cache):
        cache.get.return_value = None
        generate.side_effect = TimeoutError()
        j = job()
        diagnostics = {}
        self.assertEqual(rerank([j], {'intent_text': 'Climate policy'}, [j], diagnostics), [])
        self.assertEqual(diagnostics['reranker'], 'review_unavailable')


from django.test import TestCase
from jobs.models import Job, Organization
from jobs.services.job_scout_service import JobScoutService


class CombinedReviewTests(TestCase):
    @override_settings(SCOUT_RERANK_ENABLED=True, SCOUT_RERANK_BACKEND='deepseek', DEEPSEEK_API_KEY='test')
    def test_strict_and_provisional_candidates_share_one_review_before_limit(self):
        org = Organization.objects.create(name='Evidence review fixture')
        strict = Job.objects.create(title='Python Engineer', slug='strict-python-review', organization=org, source=Job.Source.MANUAL)
        provisional = Job.objects.create(title='Full Stack Developer', slug='provisional-python-review', organization=org, source=Job.Source.MANUAL)
        strict.scout_caveats = ['review_remote', 'review_location', 'review_seniority']
        provisional.scout_caveats = ['location_unconfirmed']
        with patch.object(JobScoutService, '_rank_candidates', side_effect=[([strict], [strict]), ([provisional], [provisional])]), \
             patch('jobs.services.scout_reranker.rerank', return_value=[strict, provisional]) as review, \
             patch('jobs.services.scout_close_matches.close_matches', return_value=[]):
            result = JobScoutService.get_matches_for_preferences({}, limit=1)
        self.assertEqual(review.call_count, 1)
        self.assertEqual([j.pk for j in review.call_args.args[0]], [strict.pk, provisional.pk])
        self.assertTrue(review.call_args.kwargs['relaxed'])
        self.assertEqual([j.pk for j in result], [provisional.pk])
