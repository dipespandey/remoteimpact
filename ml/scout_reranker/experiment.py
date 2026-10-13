"""Train small relevance classifiers and evaluate without paid provider calls.

Run: python -m ml.scout_reranker.experiment --data ml/data/scout-v1 \
     --output ml/artifacts/scout-v1 --epochs 3
"""
import argparse
import hashlib
import json
import math
import random
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score, roc_auc_score
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MAX_TOKENS = 320
BASES = {'minilm': 'cross-encoder/ms-marco-MiniLM-L6-v2', 'gte': 'thenlper/gte-small'}
BASE_REVISIONS = {'cross-encoder/ms-marco-MiniLM-L6-v2': '233902d25c440f23af6f7d6e94d2946bac0bee0a',
                  'thenlper/gte-small': '17e1f347d17fe144873b1201da91788898c639cd',
                  'sentence-transformers/all-MiniLM-L6-v2': '1110a243fdf4706b3f48f1d95db1a4f5529b4d41'}


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().split('\n') if line.strip()]


def metrics(rows, scores, threshold=None):
    labels = np.array([row['label'] for row in rows])
    groups = defaultdict(list)
    for row, score in zip(rows, scores):
        groups[row['query_id']].append((float(score), row['label']))
    ndcg = []
    top = []
    for values in groups.values():
        if not any(label for _, label in values):
            continue
        ordered = sorted(values, key=lambda item: item[0], reverse=True)
        dcg = sum(label / math.log2(position + 2) for position, (_, label) in enumerate(ordered[:3]))
        ideal = sum(label / math.log2(position + 2) for position, label in enumerate(sorted((label for _, label in values), reverse=True)[:3]))
        ndcg.append(dcg / ideal)
        top.append(ordered[0][1])
    result = {'pairs': len(rows), 'query_groups': len(groups), 'auc': float(roc_auc_score(labels, scores)), 'average_precision': float(average_precision_score(labels, scores)), 'ndcg_at_3': float(np.mean(ndcg)), 'top_1_accuracy': float(np.mean(top))}
    if threshold is not None:
        selected = np.asarray(scores) >= threshold
        tp = int(np.sum(selected & (labels == 1)))
        fp = int(np.sum(selected & (labels == 0)))
        fn = int(np.sum(~selected & (labels == 1)))
        result.update({'threshold': float(threshold), 'precision': tp / max(1, tp + fp), 'recall': tp / max(1, tp + fn), 'false_positives': fp, 'false_negatives': fn, 'selected': int(selected.sum())})
    return result


def calibrate(rows, scores, min_precision=0.97):
    candidates = []
    for threshold in np.linspace(0.05, 0.995, 190):
        result = metrics(rows, scores, threshold)
        if result['precision'] >= min_precision and result['selected'] >= 10:
            candidates.append(result)
    if not candidates:
        return {'threshold': 1.0, 'precision': 0.0, 'recall': 0.0, 'accepted': False}
    best = max(candidates, key=lambda row: (row['recall'], row['precision'], -row['threshold']))
    best['accepted'] = True
    return best


def tokens(tokenizer, rows, device):
    return tokenizer([row['query'][:1200] for row in rows], [row['text'][:4200] for row in rows], padding=True, truncation=True, max_length=MAX_TOKENS, return_tensors='pt').to(device)


def predict(model, tokenizer, rows, device, batch_size=32):
    model.eval()
    output = []
    with torch.inference_mode():
        for start in range(0, len(rows), batch_size):
            logits = model(**tokens(tokenizer, rows[start:start + batch_size], device)).logits.flatten()
            output.extend(torch.sigmoid(logits).float().cpu().tolist())
    return output


def evaluate_embeddings(name, rows, device):
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(name, device=device, revision=BASE_REVISIONS.get(name))
    model.max_seq_length = MAX_TOKENS
    queries = list(dict.fromkeys(row['query'] for row in rows))
    texts = list(dict.fromkeys(row['text'] for row in rows))
    q = model.encode(queries, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
    t = model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
    queries = {query: vector for query, vector in zip(queries, q)}
    texts = {text: vector for text, vector in zip(texts, t)}
    scores = [float(np.dot(queries[row['query']], texts[row['text']])) for row in rows]
    return metrics(rows, scores)


def cpu_latency(model, tokenizer, rows):
    model.to('cpu').eval()
    torch.set_num_threads(1)
    batches = [rows[:12], rows[12:24], rows[24:36]]
    predict(model, tokenizer, batches[0], 'cpu', batch_size=12)
    times = []
    for batch in batches:
        start = time.perf_counter()
        predict(model, tokenizer, batch, 'cpu', batch_size=12)
        times.append((time.perf_counter() - start) * 1000)
    return {'batch_pairs': 12, 'threads': 1, 'median_ms': float(np.median(times)), 'hardware': 'local development machine, not VPS'}


def train(name, train_rows, dev_rows, test_rows, golden, destination, args):
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    # Fine-tune a pinned base or continue from a locally verified pilot.
    initial = getattr(args, 'init_checkpoint', None)
    source = initial or name
    source_options = {'local_files_only': True} if initial else {'revision': BASE_REVISIONS[name]}
    tokenizer = AutoTokenizer.from_pretrained(source, **source_options)
    model = AutoModelForSequenceClassification.from_pretrained(source, num_labels=1, **source_options)
    params = sum(parameter.numel() for parameter in model.parameters())
    if params > 100_000_000:
        raise ValueError('Parameter budget exceeded')
    model.to(args.device)
    baseline_dev = predict(model, tokenizer, dev_rows, args.device)
    precision_target = getattr(args, 'min_precision', 0.97)
    baseline_calibration = calibrate(dev_rows, baseline_dev, min_precision=precision_target)
    baseline_test = metrics(test_rows, predict(model, tokenizer, test_rows, args.device), baseline_calibration['threshold'])
    baseline_golden = metrics(golden, predict(model, tokenizer, golden, args.device), baseline_calibration['threshold'])
    print(json.dumps({'event': 'baseline', 'base': name, 'test': baseline_test, 'golden': baseline_golden}), flush=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.01)
    total_steps = args.epochs * math.ceil(len(train_rows) / args.batch_size)
    warmup = max(1, int(total_steps * 0.1))
    from transformers import get_linear_schedule_with_warmup
    scheduler = get_linear_schedule_with_warmup(optimizer, warmup, total_steps)
    history = []
    best = None
    destination.mkdir(parents=True, exist_ok=True)
    for epoch in range(args.epochs):
        rows = train_rows.copy()
        random.Random(args.seed + epoch).shuffle(rows)
        model.train()
        loss_sum = 0.0
        start = time.perf_counter()
        for offset in range(0, len(rows), args.batch_size):
            batch = rows[offset:offset + args.batch_size]
            target = torch.tensor([row['label'] for row in batch], device=args.device, dtype=torch.float32)
            optimizer.zero_grad(set_to_none=True)
            logits = model(**tokens(tokenizer, batch, args.device)).logits.flatten()
            weights = torch.tensor([float(row.get('weight', 1.0)) for row in batch], device=args.device)
            if not bool(torch.all((weights > 0) & (weights <= 1))):
                raise ValueError('Training weights must be in (0, 1]')
            pair_loss = torch.nn.functional.binary_cross_entropy_with_logits(logits, target, reduction='none')
            loss = (pair_loss * weights).sum() / weights.sum()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            loss_sum += float(loss.detach().cpu()) * len(batch)
            if offset // args.batch_size % 100 == 0:
                print(json.dumps({'event': 'progress', 'epoch': epoch + 1, 'pairs': offset, 'total': len(rows)}), flush=True)
        scores = predict(model, tokenizer, dev_rows, args.device)
        calibration = calibrate(dev_rows, scores, min_precision=precision_target)
        dev = metrics(dev_rows, scores, calibration['threshold'])
        record = {'epoch': epoch + 1, 'loss': loss_sum / len(rows), 'seconds': time.perf_counter() - start, 'dev': dev}
        history.append(record)
        print(json.dumps({'event': 'epoch', 'base': name, **record}), flush=True)
        # Only development labels choose the checkpoint or acceptance threshold.
        quality = (calibration['accepted'], dev['recall'] if calibration['accepted'] else 0,
                   dev['average_precision'], dev['ndcg_at_3'])
        if getattr(args, 'selection_metric', 'precision_recall') == 'average_precision':
            quality = (dev['average_precision'], dev['ndcg_at_3'])
        if best is None or quality > best:
            best = quality
            chosen_calibration = calibration
            model.save_pretrained(destination, safe_serialization=True)
            tokenizer.save_pretrained(destination)
    del optimizer
    del model
    if args.device == 'mps':
        torch.mps.empty_cache()
    model = AutoModelForSequenceClassification.from_pretrained(destination).to(args.device)
    threshold = chosen_calibration['threshold']
    label_sources = sorted({row['label_source'] for row in train_rows})
    result = {'base_model': name, 'base_revision': BASE_REVISIONS[name], 'initial_checkpoint': initial,
              'initial_checkpoint_sha256': getattr(args, 'initial_sha256', None),
              'parameters': params, 'seed': args.seed, 'max_tokens': MAX_TOKENS, 'label_source': '+'.join(label_sources),
              'development_precision_target': precision_target,
              'checkpoint_selection': getattr(args, 'selection_metric', 'precision_recall'),
              'calibration': chosen_calibration, 'baseline_test': baseline_test, 'baseline_golden': baseline_golden,
              'test': metrics(test_rows, predict(model, tokenizer, test_rows, args.device), threshold),
              'golden': metrics(golden, predict(model, tokenizer, golden, args.device), threshold),
              'history': history, 'cpu_latency': cpu_latency(model, tokenizer, golden)}
    # Weak labels and developer-authored examples cannot authorize production activation.
    result['production_ready'] = False
    result['activation_blockers'] = ['Needs independently human-reviewed relevance judgments', 'Needs VPS latency/concurrency measurement', 'Needs real-brief comparison against current DeepSeek decisions']
    model_path = destination / 'model.safetensors'
    result['weights_sha256'] = hashlib.sha256(model_path.read_bytes()).hexdigest()
    result['version'] = result['weights_sha256'][:16]
    (destination / 'remoteimpact_model.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'event': 'complete', 'base': name, 'test': result['test'], 'golden': result['golden'], 'latency': result['cpu_latency']}), flush=True)
    del model
    if args.device == 'mps':
        torch.mps.empty_cache()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--epochs', type=int, default=3)
    parser.add_argument('--batch-size', type=int, default=24)
    parser.add_argument('--learning-rate', type=float, default=2e-5)
    parser.add_argument('--seed', type=int, default=20261013)
    parser.add_argument('--device', default='mps' if torch.backends.mps.is_available() else 'cpu')
    parser.add_argument('--bases', nargs='+', choices=list(BASES), default=['minilm', 'gte'])
    parser.add_argument('--max-train', type=int, default=0)
    parser.add_argument('--init-checkpoint', help='Local checkpoint for continued training (one base only)')
    parser.add_argument('--skip-embedding-baselines', action='store_true')
    parser.add_argument('--selection-metric', choices=['precision_recall','average_precision'], default='precision_recall')
    parser.add_argument('--min-precision',type=float,default=0.97)
    args = parser.parse_args()
    if not 0 < args.min_precision <= 1:
        raise ValueError('Invalid development precision target')
    if args.init_checkpoint:
        if len(args.bases)!=1:
            raise ValueError('A continued checkpoint requires exactly one base')
        card=json.loads((Path(args.init_checkpoint)/'remoteimpact_model.json').read_text())
        digest=hashlib.sha256((Path(args.init_checkpoint)/'model.safetensors').read_bytes()).hexdigest()
        if digest!=card['weights_sha256'] or card['base_model']!=BASES[args.bases[0]]:
            raise ValueError('Initial checkpoint identity or checksum mismatch')
        args.initial_sha256=digest
    torch.set_num_threads(4)
    directory = Path(args.data)
    dataset = json.loads((directory / 'dataset.json').read_text())
    for split in ['train', 'dev', 'test']:
        expected = dataset.get('splits', {}).get(split, {}).get('sha256')
        if expected and hashlib.sha256((directory / f'{split}.jsonl').read_bytes()).hexdigest() != expected:
            raise ValueError('Training dataset content differs from its manifest')
    train_rows, dev_rows, test_rows = [read_rows(directory / f'{name}.jsonl') for name in ['train', 'dev', 'test']]
    if args.max_train:
        train_rows = train_rows[:args.max_train]
    golden = read_rows(Path(__file__).with_name('acceptance.jsonl'))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    report = {'production_embedding': {'name': 'thenlper/gte-small', 'parameters': 33360000, 'dimensions': 384}, 'dataset': dataset, 'embedding_baselines': {}, 'classifiers': {}}
    for name in ([] if args.skip_embedding_baselines else ['thenlper/gte-small', 'sentence-transformers/all-MiniLM-L6-v2']):
        report['embedding_baselines'][name] = {'test': evaluate_embeddings(name, test_rows, args.device), 'golden': evaluate_embeddings(name, golden, args.device)}
        print(json.dumps({'event': 'embedding', 'name': name, **report['embedding_baselines'][name]}), flush=True)
        if args.device == 'mps':
            torch.mps.empty_cache()
    for key in args.bases:
        report['classifiers'][key] = train(BASES[key], train_rows, dev_rows, test_rows, golden, output / key, args)
        (output / 'evaluation.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
