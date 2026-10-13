import copy

from django.test import SimpleTestCase

from ml.scout_reranker.curation import accepted_queries, agreed_pairs, validate_partitions, remove_near_duplicates
from ml.scout_reranker.curriculum_training import exact_calibration, group_batches, grouped_loss
from . import test_teacher_training_data


class ScoutCurriculumTests(SimpleTestCase):
    def test_queries_must_be_distinct_grounded_and_contact_free(self):
        task = {'payload': {'listing': 'Build Python software services and maintain reliable APIs.'},
                'meta': {'split': 'train', 'anchor_id': 1}}
        rows = [{'kind': kind, 'query': query, 'quote': 'Build Python software services'}
                for kind, query in [('short_broad', 'Find software engineering work focused on backend services.'),
                    ('transferable', 'I want to build reliable APIs and platform tools.'),
                    ('precise_exclusion', 'I want to develop software services, excluding recruitment work.')]]
        self.assertEqual(len(accepted_queries(task, {'output': {'briefs': rows}})), 3)
        for update in [{'quote': 'Made up responsibilities'}, {'query': 'Write to person@example.org'},
                       {'query': rows[1]['query']}, {'kind': rows[1]['kind']}]:
            changed = copy.deepcopy(rows)
            changed[0].update(update)
            with self.assertRaises(ValueError):
                accepted_queries(task, {'output': {'briefs': changed}})

    def test_disagreement_and_reviewer_uncertainty_are_dropped(self):
        task = test_teacher_training_data.BetterDataTests().label_task()
        judgments = [
            {'id': 1, 'judgment': 'relevant', 'quote': 'build python software', 'reason': 'Core duties fit'},
            {'id': 2, 'judgment': 'irrelevant', 'quote': 'recruit software engineers', 'reason': 'Recruiting'},
            {'id': 3, 'judgment': 'irrelevant', 'quote': 'unclear programme role', 'reason': 'Different work'}]
        primary = {'served_model': 'pro', 'output': {'decisions': judgments}}
        second = copy.deepcopy(judgments)
        second[1]['judgment'] = 'relevant'
        second[2]['judgment'] = 'uncertain'
        second[2]['quote'] = ''
        rows, stats = agreed_pairs(task, primary, {'served_model': 'flash', 'output': {'decisions': second}})
        self.assertEqual([(row['job_id'], row['label']) for row in rows], [(1, 1)])
        self.assertEqual(stats['disagreements'], 2)
        self.assertFalse(rows[0]['human_reviewed'])

    def test_confirmation_employers_queries_and_documents_cannot_leak(self):
        row = {'organization_group': 'a', 'query': 'Find software engineering work', 'text': 'Python API role'}
        validate_partitions({'train': [row]}, [])
        for leaked in [{**row, 'query': 'Other search', 'text': 'Different listing'},
                       {**row, 'organization_group': 'b', 'text': 'Different listing'},
                       {**row, 'organization_group': 'b', 'query': 'Other search'}]:
            with self.assertRaises(ValueError):
                validate_partitions({'train': [row]}, [leaked])

    def test_exact_threshold_sweep_can_select_between_old_grid_points(self):
        rows = [{'label': 1} for _ in range(10)] + [{'label': 0} for _ in range(2)]
        scores = [0.9931] * 10 + [0.9929] * 2
        calibrated = exact_calibration(rows, scores, min_precision=0.97)
        self.assertEqual(calibrated['threshold'], 0.9931)
        self.assertEqual(calibrated['recall'], 1)
        self.assertEqual(calibrated['selected'], 10)
        self.assertFalse(exact_calibration(rows, [0.99] * 12, min_precision=0.97)['accepted'])

    def test_query_groups_are_never_split_across_batches(self):
        rows = [{'query_id': name, 'label': index % 2} for name in ['a', 'b', 'c'] for index in range(3)]
        batches = list(group_batches(rows, 42, max_pairs=6))
        owners = {}
        for number, batch in enumerate(batches):
            self.assertLessEqual(len(batch), 6)
            for row in batch:
                self.assertEqual(owners.setdefault(row['query_id'], number), number)
        self.assertEqual(sum(map(len, batches)), len(rows))
        self.assertEqual(batches, list(group_batches(rows, 42, max_pairs=6)))

    def test_likely_reposts_are_removed_without_using_evaluation_labels(self):
        public = ' '.join(f'word{index}' for index in range(100))
        repost = public.replace('word99', 'changed99')
        different = ' '.join(f'other{index}' for index in range(100))
        train = [{'text': repost, 'label': 1}, {'text': repost, 'label': 0},
                 {'text': different, 'label': 1}]
        kept, removed = remove_near_duplicates(train, [public])
        self.assertEqual(kept, [train[-1]])
        self.assertEqual(removed, 2)

    def test_ranking_penalizes_reversed_order_and_has_finite_gradients(self):
        import torch
        batch = [{'query_id': 'a', 'label': 1}, {'query_id': 'a', 'label': 0}]
        good = torch.tensor([2., -2.], requires_grad=True)
        bad = torch.tensor([-2., 2.], requires_grad=True)
        weight = torch.tensor(1.)
        good_loss = grouped_loss(good, batch, weight, 0.25)
        bad_loss = grouped_loss(bad, batch, weight, 0.25)
        self.assertLess(float(good_loss.detach()), float(bad_loss.detach()))
        bad_loss.backward()
        self.assertTrue(bool(torch.isfinite(bad.grad).all()))
        # A group with no positive is still trained by BCE, not discarded.
        negatives = [{**row, 'label': 0} for row in batch]
        self.assertTrue(bool(torch.isfinite(grouped_loss(good, negatives, weight, 0.25))))
