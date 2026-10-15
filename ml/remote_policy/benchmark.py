"""Same-input comparisons, explicit coverage, source strata and warm latency."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import time

import numpy as np
import torch

from ml.remote_policy.core import Candidate, rules
from ml.remote_policy.data import ROOT, read_jsonl, write_jsonl
from ml.remote_policy.experiment import Classifier, EMBEDDING, EMBEDDING_REVISION, embedding_features, metrics


def subset(rows, each=4):
    groups = defaultdict(list)
    for row in rows: groups[(row['source'], row['family'], row['label'])].append(row)
    selected = []
    for key in sorted(groups):
        values = sorted(groups[key], key=lambda r: hashlib.sha256(r['id'].encode()).hexdigest())
        selected.extend(values[:each])
    return selected


def freeze(root):
    paths = []
    for split in ('dev', 'test'):
        target = root / f'llm_{split}.jsonl'
        write_jsonl(target, subset(read_jsonl(root / f'{split}.jsonl')))
        paths.append(target)
    manifest = {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'n': len(read_jsonl(p))} for p in paths}
    (root / 'llm_split_manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


def summarize(rows, predictions, timings):
    result = metrics(rows, predictions)
    for key in ('source', 'family'):
        groups = defaultdict(list)
        for i, row in enumerate(rows): groups[row[key]].append(i)
        result['by_' + key] = {name: metrics([rows[i] for i in ids], [predictions[i] for i in ids]) for name, ids in groups.items()}
    if timings:
        result['latency_ms'] = {'p50': float(np.median(timings)), 'p95': float(np.percentile(timings, 95)),
                                'max': max(timings), 'hardware': 'Apple Silicon development machine; not VPS'}
    return result


def run(root, artifact, split, llm_models, output, device='cpu'):
    torch.set_num_threads(1)
    if split == 'dev': rows = read_jsonl(root / 'llm_dev.jsonl')
    elif split == 'test': rows = read_jsonl(root / 'llm_test.jsonl') + read_jsonl(root / 'public_challenge.jsonl') + read_jsonl(root / 'original_challenge.jsonl')
    elif split == 'confirmation': rows = read_jsonl(root / 'confirmation.jsonl')
    else: rows = read_jsonl(root / 'test.jsonl')
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'split': split, 'n': len(rows), 'input_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
              'independent_human_labels': 0, 'results': {}, 'predictions': {}}
    report['implementation_sha256'] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                       for name in ('core.py', 'pipeline.py', 'llm.py')}
    rule_predictions, rule_decisions, times = [], [], []
    for row in rows:
        start = time.perf_counter()
        d = rules(row['text'], Candidate(**row['candidate']), location=row.get('location', ''))
        rule_predictions.append(d.label); rule_decisions.append(d); times.append((time.perf_counter() - start) * 1000)
    report['results']['rules'] = summarize(rows, rule_predictions, times)
    report['predictions']['rules'] = rule_predictions
    print('rules', json.dumps(report['results']['rules']), flush=True)
    for name in ('minilm', 'minilm-v2', 'gte'):
        directory = artifact / name
        if not (directory / 'manifest.json').exists(): continue
        model = Classifier(directory, device=device)
        p = model.predict(rows)
        # Warm one-at-a-time inference on the first 16 cases; startup separate.
        model.predict(rows[:1]); times = []
        for row in rows[:16]:
            start = time.perf_counter(); model.predict([row]); times.append((time.perf_counter() - start) * 1000)
        report['results'][name] = summarize(rows, p, times)
        report['results'][name]['weights_sha256'] = model.manifest['weights_sha256']
        report['predictions'][name] = p
        print(name, report['results'][name]['accuracy'], flush=True)
        del model
    embedding_path = artifact / 'embeddings/embedding-logreg.joblib'
    if embedding_path.exists():
        from sentence_transformers import SentenceTransformer
        import joblib
        # joblib artifact is locally produced by experiment.py, not downloaded.
        encoder = SentenceTransformer(EMBEDDING, revision=EMBEDDING_REVISION, device=device, local_files_only=True)
        encoder.max_seq_length = 256
        clf = joblib.load(embedding_path)
        start = time.perf_counter(); x = embedding_features(encoder, rows); p = clf.predict(x).tolist()
        report['results']['embeddings_logreg'] = summarize(rows, p, [])
        report['results']['embeddings_logreg']['batch_ms'] = (time.perf_counter() - start) * 1000
        report['predictions']['embeddings_logreg'] = p
        print('embeddings', report['results']['embeddings_logreg']['accuracy'], flush=True)
        del encoder
    for name in llm_models:
        from ml.remote_policy.llm import LocalLLM
        model = LocalLLM(name); p = []; decisions = []; times = []; cached = 0; errors = 0
        for i, row in enumerate(rows):
            try:
                d, timing = model.predict(row['text'], Candidate(**row['candidate']), location=row.get('location', ''))
                p.append(d.label); decisions.append(d); times.append(timing['ms']); cached += int(timing['cached'])
            except Exception as exc:
                # Keep failed requests in coverage/accuracy denominator.
                from ml.remote_policy.core import Decision
                p.append('unknown'); decisions.append(Decision()); errors += 1
                print(json.dumps({'model': name, 'id': row['id'], 'error_type': type(exc).__name__}), flush=True)
            if (i + 1) % 20 == 0: print(name, i + 1, '/', len(rows), flush=True)
        report['results'][name] = summarize(rows, p, times)
        report['results'][name].update({'cached_calls': cached, 'request_errors': errors, 'model_digest': model.digest})
        report['predictions'][name] = p
        from ml.remote_policy.pipeline import combine, should_fallback
        hybrid = [combine(r, d, Candidate(**row['candidate']), row['text']).label for r, d, row in zip(rule_decisions, decisions, rows)]
        report['results']['rules+' + name] = summarize(rows, hybrid, [])
        report['results']['rules+' + name]['fallback_fraction'] = sum(should_fallback(r, Candidate(**row['candidate']), row['text']) for r, row in zip(rule_decisions, rows)) / len(rows)
        report['predictions']['rules+' + name] = hybrid
        output.write_text(json.dumps(report, indent=2))
        print(name, report['results'][name]['accuracy'], 'hybrid', report['results']['rules+' + name]['accuracy'], flush=True)
    report['cases'] = [{'id': r['id'], 'expected': r['label'], 'source': r['source'], 'family': r['family']} for r in rows]
    output.write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('action', choices=['freeze', 'dev', 'test', 'full-test', 'confirmation'])
    p.add_argument('--data', type=Path, default=ROOT); p.add_argument('--artifacts', type=Path, default=Path('ml/artifacts/remote-policy'))
    p.add_argument('--llm', action='append', default=[]); p.add_argument('--device', default='cpu')
    p.add_argument('--output', type=Path, default=Path('ml/data/remote-policy/report.json'))
    a = p.parse_args()
    if a.action == 'freeze': freeze(a.data)
    else: run(a.data, a.artifacts, a.action, a.llm, a.output, a.device)
