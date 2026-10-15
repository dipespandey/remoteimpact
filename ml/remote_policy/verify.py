"""Verify local research artifacts and export text-free release evidence."""
import hashlib
import json
from pathlib import Path

from ml.remote_policy.data import ROOT, read_jsonl


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(root=ROOT, artifacts=Path('ml/artifacts/remote-policy')):
    source = json.loads((root / 'raw/manifest.json').read_text())
    for entry in source['files']:
        path = root / 'raw' / Path(entry['path']).name
        assert digest(path) == entry['sha256'], f'Source changed: {path}'
    assert len(read_jsonl(root / 'raw/jobs.jsonl')) == source['unique_jobs'] == 4389
    data = json.loads((root / 'split_manifest.json').read_text())
    for name, item in data['splits'].items():
        path = root / f'{name}.jsonl'
        assert digest(path) == item['sha256']
        assert len(read_jsonl(path)) == item['rows']
    from ml.remote_policy.challenges import build as build_challenges
    from ml.remote_policy.confirmation import build as build_confirmation
    # Verify offsets/hash provenance directly, without changing any frozen files.
    jobs = {r['id']: r for r in read_jsonl(root / 'raw/jobs.jsonl')}
    from ml.remote_policy.core import clean
    for annotation_file in ('public_annotations.json', 'confirmation_annotations.json'):
        for annotation in json.loads(Path(__file__).with_name(annotation_file).read_text()):
            excerpt = clean(jobs[annotation['source_id']]['jobDescription'])[annotation['start']:annotation['end']]
            assert hashlib.sha256(excerpt.encode()).hexdigest() == annotation['sha256']
    models = {}
    from ml.remote_policy.experiment import Classifier
    import torch
    torch.set_num_threads(1)
    for name in ('minilm', 'minilm-v2', 'gte'):
        path = artifacts / name; manifest = json.loads((path / 'manifest.json').read_text())
        assert digest(path / 'model.safetensors') == manifest['weights_sha256']
        assert manifest['production_ready'] is False
        model = Classifier(path)
        assert sum(p.numel() for p in model.model.parameters()) == manifest['parameters']
        assert model.model.config.num_labels == 3
        long_case = {'candidate': {'country': 'NP'}, 'text': ('General responsibilities and qualifications. ' * 500) + ' Remote within Canada.'}
        assert model.predict([long_case]) == ['unknown'], 'Truncation guard failed'
        del model
        best = max(manifest['history'], key=lambda row: row['macro_f1'])
        models[name] = {'base': manifest['base'], 'base_revision': manifest['base_revision'],
                        'parameters': manifest['parameters'], 'weights_sha256': manifest['weights_sha256'],
                        'selected_epoch': best['epoch'], 'development': best, 'production_ready': False}
    report_names = ['dev-final', 'test-report', 'diagnostic-v2', 'release-regression', 'release-full-diagnostic']
    reports = {name: json.loads((root / f'{name}.json').read_text()) for name in report_names}
    release = reports['release-regression']
    for filename, value in release['implementation_sha256'].items():
        assert digest(Path(__file__).with_name(filename)) == value, f'Implementation differs from release report: {filename}'
    cloud = json.loads((root / 'cloud-report.json').read_text())
    for filename, value in cloud['implementation_sha256'].items():
        assert digest(Path(__file__).with_name(filename)) == value, f'Implementation differs from VPS comparison: {filename}'
    assert all(r['source'] == 'confirmation_original' for r in read_jsonl(root / 'cloud-input.jsonl'))
    assert digest(root / 'cloud-input.jsonl') == cloud['input_sha256']
    summary = {
        'date': '2026-10-15', 'status': 'experimental', 'production_ready': False,
        'independent_human_labels': 0,
        'selected_architecture': 'Deterministic policy checks plus guarded language-model fallback.',
        'vps_fallback': 'deepseek-flash, explicit opt-in API', 'offline_fallback': 'qwen3.5:4b, explicit local Ollama model',
        'dataset': {'id': source['dataset'], 'revision': source['revision'], 'unique_jobs': source['unique_jobs'],
                    'source_files': len(source['files']), 'license': 'Jobicy Dataset Terms; employer text not relicensed',
                    'split_manifest': data},
        'trained_models': models,
        'evaluations': {name: {'split': r['split'], 'n': r['n'], 'input_sha256': r['input_sha256'],
                              'results': {backend: {key: value for key, value in scores.items() if key != 'by_family'}
                                          for backend, scores in r['results'].items()}}
                        for name, r in reports.items()},
        'evaluation_status': {
            'dev-final': 'Development; not test evidence', 'test-report': 'Initial held-out diagnostic, subsequently consumed for tuning',
            'diagnostic-v2': 'Post-diagnosis regression; not untouched holdout',
            'release-regression': '68-case regression after confirmation-driven fixes; NOT independent test accuracy',
            'release-full-diagnostic': 'Previously consumed weak-labelled test, final implementation'},
        'first_confirmation': {'n': 68, 'correct': 66, 'false_compatible': 1, 'false_incompatible': 1,
                               'label_origin': 'Assistant-authored and assistant-reviewed source excerpts; no independent human labels'},
        'vps_cloud_comparison': {k: v for k, v in cloud.items() if k != 'cases'},
        'implementation_sha256': release['implementation_sha256'],
        'limitations': ['No independent human or customer validation.', 'Full-posting commercial text rights are not established by dataset metadata license.',
                        'Initial test and confirmation cases were consumed during diagnosis; post-fix scores are regressions.',
                        'City/province, complex conditions and indirect policies can remain unknown.',
                        'API tests used original synthetic data only; no employer or private candidate text was uploaded.',
                        'Local 4B model does not fit comfortably alongside current VPS workloads.',
                        'No production routes, live Scout settings, database changes or production model activation.'],
    }
    target = Path(__file__).with_name('results.json'); target.write_text(json.dumps(summary, indent=2) + '\n')
    selection = {'production_ready': False, 'architecture': summary['selected_architecture'],
                 'vps_fallback': summary['vps_fallback'], 'offline_fallback': summary['offline_fallback'],
                 'decision_basis': 'Paired diagnostic comparisons, subsequent regression checks, and VPS memory/latency measurement; no independent human validation.',
                 'source_evidence': str(target), 'implementation_sha256': release['implementation_sha256']}
    (artifacts / 'release.json').write_text(json.dumps(selection, indent=2))
    print(json.dumps({'verified_source_files': len(source['files']), 'verified_checkpoints': list(models),
                      'release_results': str(target), 'cloud_upper_bound_usd': cloud['upper_bound_usd_at_verified_rates']}, indent=2))


if __name__ == '__main__': verify()
