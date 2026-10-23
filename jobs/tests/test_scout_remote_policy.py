import json
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import caches
from django.test import SimpleTestCase, TestCase, override_settings

from jobs.services.scout_remote_policy import check_matches, requested_candidate, CAVEAT
from jobs.services.scout_workflow import bounded_workflow

cache = caches['scout_decisions']


def job(pk=1, text='Build services.', location='Remote'):
    return SimpleNamespace(pk=pk, title='Software Engineer', description=text,
                           requirements='', location=location, raw_data={})


@override_settings(SCOUT_REMOTE_POLICY_ENABLED=True, DEEPSEEK_API_KEY='test', DEEPSEEK_MODEL='test')
class RemotePolicyTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.prefs = {'locations': ['Canada']}

    def review(self, jobs, prefs=None):
        diagnostics = {}
        with bounded_workflow(diagnostics):
            result = check_matches(jobs, prefs or self.prefs, diagnostics)
        return result, diagnostics

    def test_country_request_does_not_invent_applicant_details(self):
        candidate = requested_candidate(self.prefs)
        self.assertEqual(candidate.country, 'CA')
        self.assertIsNone(candidate.authorized_countries)
        self.assertIsNone(candidate.subdivision)
        self.assertIsNone(candidate.utc_offset)
        for locations in [[], ['Europe'], ['Worldwide'], ['Canada', 'USA'], ['Georgia']]:
            self.assertIsNone(requested_candidate({'locations': locations}))

    @patch('jobs.services.scout_workflow.generate')
    def test_rules_filter_conflicts_without_api(self, model):
        allowed = job(location='Remote Canada')
        denied = job(pk=2, location='Remote US only')
        result, diag = self.review([allowed, denied])
        self.assertEqual(result, [allowed])
        self.assertEqual(diag['remote_policy_counts'], {'compatible': 1, 'incompatible': 1, 'unknown': 0})
        model.assert_not_called()

    @patch('jobs.services.scout_workflow.generate')
    def test_missing_authorization_cannot_be_resolved_by_model(self, model):
        item = job(text='Remote worldwide. US work authorization is required.')
        result, diag = self.review([item])
        self.assertEqual(result, [item])
        self.assertIn(CAVEAT, item.scout_caveats)
        self.assertTrue(item.scout_needs_confirmation)
        self.assertEqual(item.scout_remote_policy['label'], 'unknown')
        model.assert_not_called()

    @patch('jobs.services.scout_workflow.generate')
    def test_state_and_hours_stay_unknown(self, model):
        for text, country in [('Applicants must reside in California.', 'USA'),
                              ('Remote worldwide. Must overlap US working hours.', 'Canada')]:
            item = job(text=text)
            self.review([item], {'locations': [country]})
            self.assertEqual(item.scout_remote_policy['label'], 'unknown')
        model.assert_not_called()

    @patch('jobs.services.scout_workflow.generate')
    def test_generic_remote_is_unconfirmed_without_api(self, model):
        item = job()
        self.review([item])
        self.assertIn(CAVEAT, item.scout_caveats)
        model.assert_not_called()

    @patch('jobs.services.scout_workflow.generate', return_value='{"decisions":[{"id":0,"label":"compatible","evidence_ids":[1]}]}')
    def test_source_cited_fallback_cached_and_source_edits_invalidate(self, model):
        text = 'Candidates can work wherever their home happens to be.'
        item = job(text=text)
        item.scout_caveats = ['salary_unlisted']
        result, _ = self.review([item])
        self.assertEqual(result, [item])
        self.assertEqual(item.scout_remote_policy['backend'], 'rules+deepseek')
        self.assertEqual(item.scout_remote_policy['label'], 'compatible')
        self.assertEqual(item.scout_caveats, ['salary_unlisted'])
        same = job(pk=2, text=text)
        _, diag = self.review([same])
        self.assertEqual(diag['remote_policy_cache_hits'], 1)
        self.assertEqual(model.call_count, 1)
        same.description += ' Please contact recruiting.'
        self.review([same])
        self.assertEqual(model.call_count, 2)
        kwargs = model.call_args.kwargs
        self.assertEqual(kwargs['timeout'], 3)
        self.assertEqual(kwargs['stage'], 'remote_policy')
        # Never send the user's free-form brief or identifying profile data.
        self.assertNotIn('intent_text', model.call_args.args[0])

    @patch('jobs.services.scout_workflow.generate', return_value='{"decisions":[{"id":0,"label":"compatible","evidence_ids":[1]}]}')
    def test_cache_read_outage_runs_a_fresh_policy_review(self, model):
        item = job(text='Candidates can work wherever their home happens to be.')
        with patch('jobs.services.scout_remote_policy.cache.get', side_effect=RuntimeError):
            result, diagnostics = self.review([item])
        self.assertEqual(result, [item])
        self.assertEqual(diagnostics['remote_policy_api'], 'reviewed')
        model.assert_called_once()

    @patch('jobs.services.scout_workflow.generate', return_value='{"decisions":[{"id":0,"label":"incompatible","evidence_ids":[1]}]}')
    def test_grounded_fallback_can_remove_match(self, model):
        result, diag = self.review([job(text='You must make the country of the maple leaf your permanent home.')])
        self.assertEqual(result, [])
        self.assertEqual(diag['remote_policy_counts']['incompatible'], 1)

    @patch('jobs.services.scout_workflow.generate')
    def test_malformed_and_ungrounded_outputs_retain_unknown(self, model):
        for output in ['not json', '{"decisions":[]}',
                       '{"decisions":[{"id":0,"label":"compatible","evidence_ids":[]}]}',
                       '{"decisions":[{"id":true,"label":"compatible","evidence_ids":[1]}]}',
                       '{"decisions":[{"id":0,"label":"compatible","evidence_ids":[999]}]}']:
            model.return_value = output
            item = job(text='Candidates can work wherever their home happens to be.')
            self.review([item])
            self.assertEqual(item.scout_remote_policy['label'], 'unknown')
            self.assertIn(CAVEAT, item.scout_caveats)

    @patch('jobs.services.scout_workflow.generate', side_effect=TimeoutError)
    def test_provider_outage_keeps_unknown(self, model):
        item = job(text='Candidates can work wherever their home happens to be.')
        result, diag = self.review([item])
        self.assertEqual(result, [item])
        self.assertEqual(diag['remote_policy_api'], 'unavailable')
        self.assertIn(CAVEAT, item.scout_caveats)

    @patch('jobs.services.scout_workflow.generate')
    def test_one_batch_with_six_candidates_max(self, model):
        model.return_value = json.dumps({'decisions': [
            {'id': i, 'label': 'compatible', 'evidence_ids': [1]} for i in range(6)]})
        items = [job(pk=i, text=f'Candidates can work wherever their home happens to be. Reference {i}.') for i in range(10)]
        self.review(items)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(json.loads(model.call_args.args[0])['candidates']), 6)
        self.assertTrue(all(CAVEAT in item.scout_caveats for item in items[6:]))

    @patch('jobs.services.scout_workflow.generate')
    def test_oversized_input_not_truncated_for_model(self, model):
        item = job(text='You can work remotely. ' + 'x' * 17000)
        self.review([item])
        model.assert_not_called()
        self.assertIn(CAVEAT, item.scout_caveats)

    @patch('jobs.services.scout_workflow.can_call', return_value=False)
    @patch('jobs.services.scout_workflow.generate')
    def test_budget_exhaustion_retains_unknown(self, model, available):
        item = job(text='Candidates can work wherever their home happens to be.')
        _, diag = self.review([item])
        self.assertEqual(diag['remote_policy_api'], 'budget_exhausted')
        self.assertIn(CAVEAT, item.scout_caveats)
        model.assert_not_called()

    @override_settings(SCOUT_REMOTE_POLICY_ENABLED=False)
    @patch('jobs.services.scout_workflow.generate')
    def test_disabled_flag_is_complete_rollback(self, model):
        item = job(location='Remote US only')
        result, diag = self.review([item])
        self.assertEqual(result, [item])
        self.assertEqual(diag['remote_policy'], 'disabled')
        self.assertFalse(hasattr(item, 'scout_remote_policy'))
        model.assert_not_called()

    @patch('jobs.services.scout_workflow.generate')
    def test_regions_keep_existing_behavior(self, model):
        item = job(location='Remote Europe')
        result, diag = self.review([item], {'locations': ['Europe']})
        self.assertEqual(result, [item])
        self.assertEqual(diag['remote_policy'], 'unsupported_location_request')
        model.assert_not_called()


@override_settings(SCOUT_REMOTE_POLICY_ENABLED=True, SCOUT_RERANK_ENABLED=False,
                   SCOUT_MULTISTEP_ENABLED=False)
class SharedScoutIntegrationTests(TestCase):
    @patch('jobs.services.scout_close_matches.close_matches', return_value=[])
    @patch('jobs.services.scout_reranker.rerank')
    @patch('jobs.services.scout_remote_policy.check_matches', return_value=[])
    @patch('jobs.services.job_scout_service.JobScoutService._rank_candidates', return_value=([], []))
    def test_shared_matching_calls_policy_after_close_matches(self, rank, policy, rerank, close):
        from jobs.services.job_scout_service import JobScoutService
        rerank.return_value = []
        prefs = {'locations': ['Canada']}
        diagnostics = {}
        self.assertEqual(JobScoutService.get_matches_for_preferences(prefs, diagnostics=diagnostics), [])
        policy.assert_called_once()
        self.assertEqual(policy.call_args.args[1]['locations'], ['Canada'])
        self.assertIs(policy.call_args.args[2], diagnostics)
