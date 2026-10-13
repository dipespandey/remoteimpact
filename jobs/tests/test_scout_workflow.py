import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from jobs.services.job_scout_service import JobScoutService as Scout
from jobs.services.scout_workflow import bounded_workflow, generate, plan_terms


@override_settings(DEEPSEEK_API_KEY='test', DEEPSEEK_MODEL='test', SCOUT_MULTISTEP_ENABLED=True)
class BoundedScoutWorkflowTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch('jobs.services.ai.AIClient')
    def test_provider_calls_and_timeout_are_bounded(self, client):
        diagnostics = {}
        with bounded_workflow(diagnostics):
            for _ in range(4):
                generate('test', timeout=30)
            with self.assertRaises(TimeoutError):
                generate('test')
        self.assertEqual(client.return_value.generate.call_count, 4)
        self.assertEqual(diagnostics['llm_calls'], 4)
        self.assertLessEqual(client.return_value.generate.call_args.kwargs['timeout'], 18)
        self.assertEqual(client.return_value.generate.call_args.kwargs['max_retries'], 0)

    @patch('jobs.services.scout_workflow.monotonic', side_effect=[0, 19])
    @patch('jobs.services.ai.AIClient')
    def test_expired_budget_never_calls_provider(self, client, clock):
        with bounded_workflow({}):
            with self.assertRaises(TimeoutError):
                generate('test')
        client.assert_not_called()

    @patch('jobs.services.scout_workflow.generate', return_value='{"terms":["backend developer"]}')
    def test_plan_is_cached_and_cannot_change_constraints(self, model):
        prefs = Scout._fallback_parse('Remote software engineering roles using Python in Europe, mid-level, no internships.')
        original = json.dumps(prefs, sort_keys=True)
        self.assertEqual(plan_terms(prefs, {}), ['backend developer'])
        self.assertEqual(plan_terms(prefs, {}), ['backend developer'])
        self.assertEqual(json.dumps(prefs, sort_keys=True), original)
        self.assertEqual(model.call_count, 1)

    @patch('jobs.services.scout_workflow.generate', return_value='{"terms":["developer"],"locations":["USA"]}')
    def test_malformed_plan_falls_back(self, model):
        diagnostics = {}
        self.assertEqual(plan_terms({'intent_text': 'Python in Europe'}, diagnostics), [])
        self.assertEqual(diagnostics['planning'], 'fallback')

    @patch('jobs.services.scout_close_matches.close_matches', return_value=[])
    @patch('jobs.services.scout_reranker.rerank', return_value=[])
    @patch.object(Scout, '_rank_candidates', return_value=([], []))
    @patch('jobs.services.scout_workflow.plan_terms', side_effect=[['engineer'], ['backend developer']])
    def test_one_refinement_keeps_original_preferences(self, planner, rank, rerank, close):
        prefs = Scout._fallback_parse('Remote software engineering roles using Python in Europe, mid-level, no internships.')
        diagnostics = {}
        Scout.get_matches_for_preferences(prefs, diagnostics=diagnostics)
        # Initial, refined, and existing provisional pass; no unbounded retry.
        self.assertEqual(diagnostics['retrieval_passes'], 2)
        self.assertEqual(planner.call_count, 2)
        for call in rank.call_args_list:
            self.assertEqual(call.args[1]['required_skills'], ['python'])
            self.assertEqual(call.args[1]['locations'], ['Europe'])

    @patch.object(Scout, 'get_matches_for_preferences', return_value=[])
    @patch.object(Scout, 'parse_intent', return_value={'intent_text': 'test'})
    def test_preview_entry_uses_interpretation(self, parser, matcher):
        prefs, jobs = Scout.search_intent('test')
        parser.assert_called_once_with('test')
        self.assertEqual(jobs, [])
        self.assertEqual(prefs['intent_text'], 'test')
