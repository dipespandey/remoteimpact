import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from jobs.services import local_scout_reranker as local
from jobs.services.scout_reranker import rerank
from ml.scout_reranker.data import build


def job(pk, title='Software Engineer', **extra):
    fields = dict(pk=pk, title=title, description='Build Python software services.', requirements='Python and Django experience.',
                  location='Remote worldwide', country='Worldwide', salary_min=90000, salary_max=120000,
                  salary_currency='USD', raw_data={}, experience_level='mid', job_type='full-time',
                  scout_reasons=['Existing grounded reason'])
    fields.update(extra)
    return SimpleNamespace(**fields)


@override_settings(SCOUT_RERANK_ENABLED=True, SCOUT_RERANK_BACKEND='local',
                   SCOUT_LOCAL_MODEL_PATH='/tmp/qa-small-model', SCOUT_LOCAL_ALLOW_EXPERIMENTAL=True,
                   DEEPSEEK_API_KEY='not-used')
class LocalScoutTests(SimpleTestCase):
    def setUp(self):
        self.loaded = {'path': str(Path('/tmp/qa-small-model').resolve()), 'threshold': 0.8, 'version': 'qa-v1', 'production_ready': False}
        self.preferences = {'intent_text': 'Software engineering', 'locations': ['worldwide']}

    @patch('jobs.services.ai.AIClient.generate')
    @patch.object(local, '_loaded', None)
    def test_cold_local_model_uses_rules_without_provider(self, provider):
        jobs = [job(1)]
        diagnostics = {}
        self.assertEqual(rerank(jobs, self.preferences, jobs, diagnostics), jobs)
        self.assertEqual(diagnostics['reranker'], 'local_deterministic_fallback')
        provider.assert_not_called()

    @patch('jobs.services.ai.AIClient.generate')
    @patch.object(local, 'score_pairs', return_value=[0.85, 0.99, 0.4])
    @patch.object(local, 'cache')
    def test_selects_and_orders_only_existing_candidates(self, cache, scores, provider):
        cache.get.return_value = None
        jobs = [job(1), job(2), job(3)]
        diagnostics = {}
        with patch.object(local, '_loaded', self.loaded):
            selected = rerank(jobs, self.preferences, jobs, diagnostics)
        self.assertEqual([value.pk for value in selected], [2, 1])
        self.assertEqual(selected[0].scout_reasons, ['Existing grounded reason'])
        self.assertEqual(diagnostics['local_model_version'], 'qa-v1')
        provider.assert_not_called()

    @patch.object(local, 'score_pairs', return_value=[0.99])
    @patch.object(local, 'cache')
    def test_cannot_promote_wide_pool_or_ineligible_jobs(self, cache, scores):
        cache.get.return_value = None
        valid, wrong_location, wide = job(1), job(2, location='Remote US only', country='USA'), job(3)
        preferences = {'intent_text': 'Software engineer in Spain', 'locations': ['Spain']}
        with patch.object(local, '_loaded', self.loaded):
            selected = rerank([wide, valid, wrong_location], preferences, [valid, wrong_location], {})
        self.assertEqual([value.pk for value in selected], [1])
        self.assertEqual(len(scores.call_args.args[1]), 1)

    @patch.object(local, 'score_pairs', side_effect=RuntimeError('inference failed'))
    @patch.object(local, 'cache')
    def test_inference_failure_retains_rules_and_privacy(self, cache, scores):
        cache.get.return_value = None
        diagnostics = {}
        jobs = [job(1)]
        with patch.object(local, '_loaded', self.loaded):
            self.assertEqual(rerank(jobs, self.preferences, jobs, diagnostics), jobs)
        self.assertEqual(diagnostics, {'reranker': 'local_deterministic_fallback'})
        self.assertNotIn('Software engineering', json.dumps(diagnostics))

    @patch.object(local, 'score_pairs')
    @patch.object(local, 'cache')
    def test_invalid_cached_scores_fall_back(self, cache, scores):
        cache.get.return_value = [float('nan')]
        jobs = [job(1)]
        with patch.object(local, '_loaded', self.loaded):
            self.assertEqual(rerank(jobs, self.preferences, jobs, {}), jobs)
        scores.assert_not_called()

    @override_settings(SCOUT_LOCAL_ALLOW_EXPERIMENTAL=False)
    @patch.object(local, 'score_pairs')
    def test_experimental_model_cannot_run_without_explicit_flag(self, scores):
        jobs = [job(1)]
        with patch.object(local, '_loaded', self.loaded):
            self.assertEqual(rerank(jobs, self.preferences, jobs, {}), jobs)
        scores.assert_not_called()

    @override_settings(SCOUT_LOCAL_MODEL_PATH='/tmp/different-release')
    @patch.object(local, 'score_pairs')
    def test_switching_release_cannot_use_previous_cached_model(self, scores):
        jobs = [job(1)]
        with patch.object(local, '_loaded', self.loaded):
            self.assertEqual(rerank(jobs, self.preferences, jobs, {}), jobs)
        scores.assert_not_called()

    @override_settings(SCOUT_LOCAL_ALLOW_EXPERIMENTAL=False)
    def test_preload_rejects_unaccepted_model_before_loading_weights(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(SCOUT_LOCAL_MODEL_PATH=directory):
            Path(directory, 'remoteimpact_model.json').write_text(json.dumps({'production_ready': False}))
            with patch.object(local, '_loaded', None), self.assertRaisesMessage(ValueError, 'production acceptance'):
                local.load_model()

    def test_empty_prediction_is_not_replaced_with_irrelevant_matches(self):
        with patch.object(local, '_loaded', self.loaded), patch.object(local, 'score_pairs', return_value=[0.1]), patch.object(local, 'cache') as cache:
            cache.get.return_value = None
            self.assertEqual(rerank([job(1)], self.preferences, [job(1)], {}), [])

    def test_preload_rejects_parameter_budget_before_loading_weights(self):
        card = {'production_ready': True, 'calibration': {'threshold': 0.8, 'accepted': True},
                'parameters': 100_000_001}
        with tempfile.TemporaryDirectory() as directory, override_settings(SCOUT_LOCAL_MODEL_PATH=directory):
            Path(directory, 'remoteimpact_model.json').write_text(json.dumps(card))
            with patch.object(local, '_loaded', None), self.assertRaisesMessage(ValueError, 'parameter budget'):
                local.load_model()

    def test_preload_rejects_changed_weights_before_loading_model(self):
        card = {'production_ready': True, 'calibration': {'threshold': 0.8, 'accepted': True},
                'parameters': 22_713_601, 'weights_sha256': 'incorrect', 'version': 'incorrect'}
        with tempfile.TemporaryDirectory() as directory, override_settings(SCOUT_LOCAL_MODEL_PATH=directory):
            Path(directory, 'remoteimpact_model.json').write_text(json.dumps(card))
            Path(directory, 'model.safetensors').write_bytes(b'changed weights')
            with patch.object(local, '_loaded', None), self.assertRaisesMessage(ValueError, 'checksum mismatch'):
                local.load_model()

    def test_master_flag_and_unknown_backend_do_not_call_local_model(self):
        with override_settings(SCOUT_RERANK_ENABLED=False), patch.object(local, 'score_pairs') as scores:
            self.assertEqual(rerank([job(1)], self.preferences, [job(1)], {})[0].pk, 1)
            scores.assert_not_called()
        with override_settings(SCOUT_RERANK_BACKEND='typo'), patch.object(local, 'score_pairs') as scores:
            self.assertEqual(rerank([job(1)], self.preferences, [job(1)], {})[0].pk, 1)
            scores.assert_not_called()


class TrainingDataTests(SimpleTestCase):
    def test_organization_and_duplicates_do_not_cross_splits(self):
        rows = []
        for org in range(1, 80):
            for number, title in enumerate(['Backend Software Engineer', 'Data Scientist', 'Accountant']):
                rows.append({'id': org * 10 + number, 'organization_id': org, 'title': title,
                             'description': f'{title} responsibilities with public reference {org}', 'requirements': ''})
        # Duplicate company names/IDs cannot leak identical listing content.
        rows.append({**rows[0], 'id': 9999, 'organization_id': 999})
        splits = build(rows)
        seen_orgs, seen_texts = {}, {}
        for split, pairs in splits.items():
            for pair in pairs:
                self.assertEqual(seen_orgs.setdefault(pair['organization_group'], split), split)
                self.assertEqual(seen_texts.setdefault(pair['text'], split), split)
                self.assertEqual(pair['label_source'], 'title_family_weak_v1')
        self.assertEqual(build(rows), splits)
        self.assertEqual(set(splits), {'train', 'dev', 'test'})

    def test_ambiguous_titles_and_contact_information_are_excluded(self):
        rows = [{'id': 1, 'organization_id': 1, 'title': 'Software Engineer and Data Scientist', 'description': 'Private details'},
                {'id': 2, 'organization_id': 2, 'title': 'Backend Software Engineer', 'description': 'Email qa@example.org or visit https://example.org/contact'},
                {'id': 3, 'organization_id': 3, 'title': 'Accountant', 'description': 'Prepare accounts'}]
        output = [pair for split in build(rows).values() for pair in split]
        self.assertTrue(output)
        self.assertTrue(all(pair['job_id'] != 1 for pair in output))
        self.assertNotIn('qa@example.org', json.dumps(output))
        self.assertNotIn('https://example.org/contact', json.dumps(output))
