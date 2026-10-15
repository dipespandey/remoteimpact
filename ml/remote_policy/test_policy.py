"""Behavior/integrity tests; these are not empirical accuracy measurements."""
import hashlib
import json
from pathlib import Path
import unittest

from ml.remote_policy.core import Candidate, Decision, clean, rules
from ml.remote_policy.pipeline import PolicyEvaluator, combine


class PolicyTests(unittest.TestCase):
    def test_country_codes_are_validated(self):
        self.assertEqual(Candidate('np').country, 'NP')
        for code in ('', 'Nepal', 'XX'):
            with self.assertRaises(ValueError): Candidate(code)
        with self.assertRaises(ValueError): Candidate('NP', utc_offset=19)

    def test_generic_remote_is_unknown(self):
        self.assertEqual(rules('Fully remote role.', Candidate('NP')).label, 'unknown')

    def test_restriction_beats_worldwide(self):
        self.assertEqual(rules('Work from anywhere. Applicants must live in Germany.', Candidate('NP')).label, 'incompatible')

    def test_country_restriction(self):
        for code, expected in [('DE', 'compatible'), ('NP', 'incompatible')]:
            self.assertEqual(rules('Remote within Germany only.', Candidate(code)).label, expected)

    def test_company_location_is_not_restriction(self):
        self.assertEqual(rules('Our headquarters are in Germany. Remote work available.', Candidate('NP')).label, 'unknown')

    def test_html_boundaries(self):
        self.assertEqual(rules('<p>Work from anywhere.</p><p>Must be based in Canada.</p>', Candidate('NP')).label, 'incompatible')

    def test_eu_and_eea_differ(self):
        self.assertEqual(rules('Remote within the EU.', Candidate('NO')).label, 'incompatible')
        self.assertEqual(rules('Remote within the EEA.', Candidate('NO')).label, 'compatible')

    def test_us_pronoun_is_not_country(self):
        self.assertEqual(rules('Work with us remotely.', Candidate('NP')).label, 'unknown')

    def test_work_authorization_not_inferred_from_residence(self):
        text = 'Work from anywhere. Applicants must be authorized to work in Canada.'
        self.assertEqual(rules(text, Candidate('CA')).label, 'unknown')
        self.assertEqual(rules(text, Candidate('NP', authorized_countries=('CA',))).label, 'compatible')
        self.assertEqual(rules(text, Candidate('CA', authorized_countries=())).label, 'incompatible')

    def test_model_cannot_invent_work_authorization(self):
        candidate = Candidate('US')
        d = rules('Remote worldwide. You must be authorized to work in Canada.', candidate)
        self.assertEqual(combine(d, Decision('incompatible', backend='fake'), candidate).label, 'unknown')

    def test_working_hours_not_implied_by_offset(self):
        self.assertEqual(rules('Work from anywhere. You must overlap four hours with US business hours.', Candidate('US', utc_offset=-5)).label, 'unknown')

    def test_missing_subdivision(self):
        text = 'Remote within California only.'
        self.assertEqual(rules(text, Candidate('US')).label, 'unknown')
        self.assertEqual(rules(text, Candidate('US', subdivision='CA')).label, 'compatible')
        self.assertEqual(rules(text, Candidate('US', subdivision='TX')).label, 'incompatible')

    def test_negated_requirement(self):
        self.assertEqual(rules('Work from anywhere. It is not required to reside in Germany.', Candidate('DE')).label, 'compatible')

    def test_unknown_fallback_failure(self):
        class Broken:
            def predict(self, *args, **kwargs): raise TimeoutError()
        d = PolicyEvaluator(Broken()).evaluate('You may work remotely subject to our residency policy.', Candidate('NP'))
        self.assertEqual(d.label, 'unknown')
        self.assertIn('Local language model unavailable', ' '.join(d.reasons))

    def test_no_fallback_when_rule_decides(self):
        class Never:
            def predict(self, *args, **kwargs): raise AssertionError('Must not call')
        self.assertEqual(PolicyEvaluator(Never()).evaluate('Remote within Canada only.', Candidate('NP')).label, 'incompatible')

    def test_evidence_is_from_source(self):
        text = 'Work from anywhere. Applicants must live in Canada.'
        d = rules(text, Candidate('NP'))
        self.assertTrue(all(e['quote'] in clean(text) for e in d.evidence))

    def test_blank_input(self):
        self.assertEqual(rules('', Candidate('NP')).label, 'unknown')

    def test_oversized_input_abstains(self):
        self.assertEqual(rules('Work from anywhere. ' * 6000, Candidate('NP')).label, 'unknown')

    def test_mixed_polarity_does_not_invert_whole_country_list(self):
        text = 'Remote within Canada or Mexico, not the United States.'
        self.assertEqual(rules(text, Candidate('CA')).label, 'compatible')
        self.assertEqual(rules(text, Candidate('US')).label, 'incompatible')

    def test_office_location_is_separate_from_remote_scope(self):
        candidate = Candidate('DE')
        text = 'The role is remote in Canada, with offices in Germany.'
        decision = rules(text, candidate)
        self.assertEqual(combine(decision, Decision('compatible', backend='fake'), candidate, text).label, 'incompatible')

    def test_unknown_state_does_not_pass_exclusions(self):
        text = 'Remote within the United States. We cannot hire in California or New York.'
        self.assertEqual(rules(text, Candidate('US')).label, 'unknown')
        self.assertEqual(rules(text, Candidate('US', subdivision='CA')).label, 'incompatible')

    def test_requires_existing_authorization(self):
        text = 'This role requires existing work authorization in Canada. Remote worldwide.'
        self.assertEqual(rules(text, Candidate('CA')).label, 'unknown')
        self.assertEqual(rules(text, Candidate('CA', authorized_countries=())).label, 'incompatible')

    def test_unrelated_negation_does_not_invert_residence(self):
        text = 'Remote within Canada; compensation is not set.'
        self.assertEqual(rules(text, Candidate('NP')).label, 'incompatible')

    def test_privacy_notice_is_not_a_state_restriction(self):
        text = 'Remote within the United States. California residents should read our privacy notice.'
        self.assertEqual(rules(text, Candidate('US', subdivision='TX')).label, 'compatible')

    def test_non_remote_not_confirmed_as_remote(self):
        text = 'This is a non-remote position based in Germany.'
        self.assertEqual(rules(text, Candidate('DE')).label, 'unknown')

    def test_invalid_model_evidence_is_rejected(self):
        from ml.remote_policy.llm import decode_decision
        segments = [{'field': 'text', 'text': 'Remote within Canada.'}]
        for content in ['{}', '{"label":"compatible","evidence_ids":[]}',
                        '{"label":"compatible","evidence_ids":[999]}',
                        '{"label":"compatible","evidence_ids":[true]}', 'not json']:
            self.assertEqual(decode_decision(content, segments, 'fake').label, 'unknown')
        result = decode_decision('{"label":"incompatible","evidence_ids":[0]}', segments, 'fake')
        self.assertEqual(result.evidence[0]['quote'], segments[0]['text'])

    def test_classifier_checksums_before_loading(self):
        import tempfile
        from ml.remote_policy.experiment import Classifier
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / 'manifest.json').write_text('{"weights_sha256":"incorrect"}')
            (p / 'model.safetensors').write_bytes(b'invalid')
            with self.assertRaises(ValueError): Classifier(p)

    def test_data_separation_and_freeze(self):
        from ml.remote_policy.data import ROOT, read_jsonl
        if not (ROOT / 'split_manifest.json').exists(): self.skipTest('Acquire and prepare the optional research data first')
        manifest = json.loads((ROOT / 'split_manifest.json').read_text())
        all_groups = {}; all_inputs = {}
        for split in ('train', 'dev', 'test'):
            path = ROOT / f'{split}.jsonl'
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), manifest['splits'][split]['sha256'])
            for row in read_jsonl(path):
                group = row['group']
                self.assertIn(all_groups.get(group, split), (split,))
                all_groups[group] = split
                key = json.dumps([row['text'], row['candidate']], sort_keys=True)
                self.assertIn(all_inputs.get(key, split), (split,))
                all_inputs[key] = split


if __name__ == '__main__': unittest.main()
