import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from jobs.services.scout_reranker import rerank
from jobs.services.scout_workflow import bounded_workflow
from jobs.tests.test_scout_pipeline import job


@override_settings(SCOUT_RERANK_ENABLED=True, SCOUT_RERANK_BACKEND='deepseek',
                   DEEPSEEK_API_KEY='test', DEEPSEEK_MODEL='repair-test')
class ReviewRepairTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.prefs = {'intent_text': 'Software engineering jobs'}
        self.a = job(1, title='Software Engineer', description='Build Python software services.', requirements='')
        self.b = job(2, title='Backend Developer', description='Maintain software integrations.', requirements='')

    def positive(self, pk):
        return {'id': pk, 'relevant': True, 'checks': {
            'function': ['confirmed', 'description',
                         self.a.description if pk == 1 else self.b.description]}}

    def response(self, *records):
        return json.dumps({'decisions': list(records)})

    def test_missing_decision_repairs_only_unknown_then_reuses_all_verified_decisions(self):
        d = {}
        with patch('jobs.services.scout_workflow.generate', side_effect=[
                self.response(self.positive(1)), self.response(self.positive(2))]) as provider:
            self.assertEqual(rerank([self.a, self.b], self.prefs, [], d), [self.a, self.b])
        self.assertEqual(provider.call_count, 2)
        self.assertEqual([row['id'] for row in json.loads(provider.call_args.args[0])['candidates']], [2])
        self.assertEqual(d['reranker'], 'model')
        self.assertEqual(d['review_repair_resolved'], 1)
        self.assertEqual(d['review_decision_issues'], {'omitted_decision': 1})
        with patch('jobs.services.scout_workflow.generate') as provider:
            self.assertEqual(rerank([self.b, self.a], self.prefs, [], {}), [self.b, self.a])
        provider.assert_not_called()

    def test_unsupported_positive_is_unknown_after_one_repair_and_never_cached_as_negative(self):
        bad = self.positive(2)
        bad['checks']['function'][2] = 'Invented matching responsibilities'
        d = {}
        with patch('jobs.services.scout_workflow.generate', side_effect=[
                self.response(self.positive(1), bad), self.response(bad)]) as provider:
            self.assertEqual(rerank([self.a, self.b], self.prefs, [], d), [self.a])
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(d['reranker'], 'review_partial')
        self.assertEqual(d['review_missing_decisions'], 1)
        self.assertEqual(d['review_decision_issues']['prompt_quote_ungrounded'], 2)
        with patch('jobs.services.scout_workflow.generate', return_value=self.response(self.positive(2))) as provider:
            self.assertEqual(rerank([self.a, self.b], self.prefs, [], {}), [self.a, self.b])
        self.assertEqual([row['id'] for row in json.loads(provider.call_args.args[0])['candidates']], [2])

    def test_grounded_conflict_is_a_rejection_and_is_cached_without_becoming_match(self):
        prefs = {**self.prefs, 'locations': ['Europe']}
        self.a.location = 'Remote Germany'
        item = self.positive(1)
        item['checks']['location'] = ['conflicting', 'location', 'Remote Germany']
        d = {}
        with patch('jobs.services.scout_workflow.generate', return_value=self.response(item)) as provider:
            self.assertEqual(rerank([self.a], prefs, [], d), [])
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(d['reranker'], 'model')
        self.assertEqual(d['review_decision_issues']['verified_conflict'], 1)
        with patch('jobs.services.scout_workflow.generate') as provider:
            self.assertEqual(rerank([self.a], prefs, [], {}), [])
        provider.assert_not_called()

    def test_ungrounded_conflict_remains_unknown_and_cannot_be_cached_as_rejection(self):
        item = self.positive(1)
        item['checks']['function'] = ['conflicting', 'description', 'Invented duties of a different profession']
        d = {}
        with patch('jobs.services.scout_workflow.generate', return_value=self.response(item)) as provider:
            self.assertEqual(rerank([self.a], self.prefs, [], d), [])
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(d['reranker'], 'review_partial')
        with patch('jobs.services.scout_workflow.generate', return_value=self.response(self.positive(1))) as provider:
            self.assertEqual(rerank([self.a], self.prefs, [], {}), [self.a])
        provider.assert_called_once()

    def test_original_source_quote_guard_survives_repair(self):
        synthetic = self.positive(1)
        synthetic['checks']['function'][2] = 'Build Python […] software services.'
        with patch('jobs.services.scout_review.evidence_fields', return_value={
                'title': self.a.title, 'description': 'Build Python […] software services.', 'requirements': ''}), \
             patch('jobs.services.scout_workflow.generate', return_value=self.response(synthetic)):
            d = {}
            self.assertEqual(rerank([self.a], self.prefs, [], d), [])
        self.assertEqual(d['reranker'], 'review_partial')
        self.assertEqual(d['review_decision_issues']['source_quote_ungrounded'], 2)

    def test_repair_failure_preserves_verified_results(self):
        d = {}
        with patch('jobs.services.scout_workflow.generate', side_effect=[
                self.response(self.positive(1)), TimeoutError]) as provider:
            self.assertEqual(rerank([self.a, self.b], self.prefs, [], d), [self.a])
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(d['review_repair_failure_type'], 'TimeoutError')
        self.assertEqual(d['reranker'], 'review_partial')

    def test_no_remaining_budget_skips_repair(self):
        with patch('jobs.services.scout_workflow.generate', return_value=self.response(self.positive(1))) as provider, \
             patch('jobs.services.scout_workflow.can_call', return_value=False):
            d = {}
            self.assertEqual(rerank([self.a, self.b], self.prefs, [], d), [self.a])
        provider.assert_called_once()
        self.assertNotIn('review_repair_attempted', d)

    def test_invented_id_cannot_be_repaired_into_match(self):
        with patch('jobs.services.scout_workflow.generate', return_value=self.response({
                **self.positive(1), 'id': 999})) as provider:
            d = {}
            self.assertEqual(rerank([self.a], self.prefs, [], d), [])
        provider.assert_called_once()
        self.assertEqual(d['reranker'], 'review_unavailable')
        self.assertEqual(d['review_decision_issues']['invalid_response'], 1)

    def test_wrong_field_or_missing_checks_stays_unknown(self):
        wrong_field=self.positive(1)
        wrong_field['checks']['function'][1]='requirements'
        missing={**self.positive(1),'checks':{}}
        for item in (wrong_field,missing):
            cache.clear()
            with patch('jobs.services.scout_workflow.generate',return_value=self.response(item)) as provider:
                self.assertEqual(rerank([self.a],self.prefs,[],{}),[])
            self.assertEqual(provider.call_count,2)

    def test_conflict_quote_in_prompt_but_not_source_remains_unknown(self):
        item = self.positive(1)
        item['checks']['function'] = ['conflicting', 'description', 'Build Python […] software services.']
        with patch('jobs.services.scout_review.evidence_fields', return_value={
                'title': self.a.title, 'description': 'Build Python […] software services.', 'requirements': ''}), \
             patch('jobs.services.scout_workflow.generate', return_value=self.response(item)):
            d = {}
            self.assertEqual(rerank([self.a], self.prefs, [], d), [])
        self.assertEqual(d['reranker'], 'review_partial')
        self.assertNotIn('verified_conflict', d['review_decision_issues'])
        with patch('jobs.services.scout_workflow.generate', return_value=self.response(self.positive(1))) as provider:
            self.assertEqual(rerank([self.a], self.prefs, [], {}), [self.a])
        provider.assert_called_once()

    @override_settings(SCOUT_CLOSE_MATCHES=True)
    def test_repair_keeps_last_call_reserved_for_alternatives(self):
        from jobs.services.scout_workflow import generate
        with patch('jobs.services.ai.AIClient') as client:
            client.return_value.generate.side_effect = ['setup', 'setup', self.response(self.positive(1))]
            d = {}
            with bounded_workflow(d):
                generate('setup')
                generate('setup')
                self.assertEqual(rerank([self.a, self.b], self.prefs, [], d, relaxed=True), [self.a])
        self.assertEqual(client.return_value.generate.call_count, 3)
        self.assertNotIn('review_repair_attempted', d)

    def test_repair_shares_existing_four_provider_call_ceiling(self):
        from jobs.services.scout_workflow import generate
        with patch('jobs.services.ai.AIClient') as client:
            client.return_value.generate.side_effect = [
                'setup', 'setup', self.response(self.positive(1)), self.response(self.positive(2))]
            d = {}
            with bounded_workflow(d):
                generate('setup')
                generate('setup')
                self.assertEqual(rerank([self.a, self.b], self.prefs, [], d), [self.a, self.b])
                with self.assertRaises(TimeoutError):
                    generate('fifth')
        self.assertEqual(client.return_value.generate.call_count, 4)
        self.assertEqual(d['llm_calls'], 4)
