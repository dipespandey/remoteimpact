"""Development-only training selection; record amendments before final evaluation."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import time


def exact_calibration(rows, scores, min_precision=0.90, minimum_selected=10):
    """Sweep actual distinct scores, keeping ties together; no coarse grid."""
    if len(rows) != len(scores) or not rows:
        raise ValueError('Calibration rows and scores must align')
    if not 0 < min_precision <= 1 or minimum_selected < 1:
        raise ValueError('Invalid calibration target')
    positives = sum(row['label'] for row in rows)
    ordered = sorted(zip(scores, rows), key=lambda pair: pair[0], reverse=True)
    selected = tp = 0
    best = None
    for index, (score, row) in enumerate(ordered):
        if not math.isfinite(score) or not 0 <= score <= 1 or row['label'] not in {0, 1}:
            raise ValueError('Invalid calibration observation')
        selected += 1
        tp += row['label']
        if index + 1 < len(ordered) and ordered[index + 1][0] == score:
            continue
        precision = tp / selected
        if precision < min_precision or selected < minimum_selected:
            continue
        candidate = {'threshold': float(score), 'precision': precision,
                     'recall': tp / max(1, positives), 'selected': selected, 'accepted': True}
        quality = (candidate['recall'], precision, -score)
        if best is None or quality > best[0]:
            best = quality, candidate
    return best[1] if best else {'threshold': 1.0, 'precision': 0.0, 'recall': 0.0,
                               'selected': 0, 'accepted': False}


def group_batches(rows, seed, max_pairs=32):
    groups = defaultdict(list)
    for row in rows:
        groups[row['query_id']].append(row)
    grouped = list(groups.values())
    random.Random(seed).shuffle(grouped)
    batch = []
    for group in grouped:
        if len(group) > max_pairs:
            raise ValueError('Query group exceeds batch capacity')
        if batch and len(batch) + len(group) > max_pairs:
            yield batch
            batch = []
        batch.extend(group)
    if batch:
        yield batch


def grouped_loss(logits, batch, positive_weight, ranking_weight):
    import torch
    grouped = defaultdict(list)
    for index, row in enumerate(batch):
        grouped[row['query_id']].append(index)
    losses = []
    for indices in grouped.values():
        target = torch.tensor([batch[index]['label'] for index in indices],
                              dtype=logits.dtype, device=logits.device)
        values = logits[indices]
        # Mild smoothing reduces overconfident fitting to synthetic judgments.
        soft_target = target * 0.98 + 0.01
        loss = torch.nn.functional.binary_cross_entropy_with_logits(
            values, soft_target, pos_weight=positive_weight)
        positive, negative = values[target == 1], values[target == 0]
        if ranking_weight and positive.numel() and negative.numel():
            ranking = torch.nn.functional.softplus(negative[:, None] - positive[None, :]).mean()
            loss = loss + ranking_weight * ranking
        losses.append(loss)
    return torch.stack(losses).mean()


def development_quality(dev, calibration):
    return (calibration['accepted'], calibration['recall'], dev['average_precision'], dev['ndcg_at_3'])


def train_config(train, dev, output, ranking_weight, args, initial=None):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup
    from .experiment import BASES, BASE_REVISIONS, metrics, predict, tokens
    torch.manual_seed(args.seed)
    name = BASES['minilm']
    source = name
    options = {'revision': BASE_REVISIONS[name]}
    initial_digest = None
    if initial:
        source = Path(initial)
        card = json.loads((source / 'remoteimpact_model.json').read_text())
        initial_digest = hashlib.sha256((source / 'model.safetensors').read_bytes()).hexdigest()
        if card['base_model'] != name or initial_digest != card['weights_sha256']:
            raise ValueError('Continued checkpoint identity or checksum mismatch')
        options = {'local_files_only': True}
    tokenizer = AutoTokenizer.from_pretrained(source, **options)
    model = AutoModelForSequenceClassification.from_pretrained(source, **options).to(args.device)
    parameters = sum(parameter.numel() for parameter in model.parameters())
    if parameters > 100_000_000:
        raise ValueError('Parameter budget exceeded')
    positive_count = sum(row['label'] for row in train)
    ratio = min(3.0, (len(train) - positive_count) / max(1, positive_count))
    positive_weight = torch.tensor(ratio, dtype=torch.float32, device=args.device)
    learning_rate = 1e-5 if initial else args.learning_rate
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=0.01)
    total_steps = sum(len(list(group_batches(train, args.seed + epoch))) for epoch in range(args.epochs))
    scheduler = get_linear_schedule_with_warmup(optimizer, max(1, total_steps // 10), total_steps)
    output.mkdir(parents=True, exist_ok=True)
    best = None
    history = []
    chosen_epoch = None
    for epoch in range(args.epochs):
        model.train()
        started = time.monotonic()
        total_loss = 0
        batches = list(group_batches(train, args.seed + epoch))
        for batch in batches:
            optimizer.zero_grad(set_to_none=True)
            logits = model(**tokens(tokenizer, batch, args.device)).logits.flatten()
            loss = grouped_loss(logits, batch, positive_weight, ranking_weight)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            total_loss += float(loss.detach().cpu())
        scores = predict(model, tokenizer, dev, args.device)
        calibration = exact_calibration(dev, scores)
        result = metrics(dev, scores, calibration['threshold'])
        record = {'epoch': epoch + 1, 'loss': total_loss / len(batches),
                  'seconds': time.monotonic() - started, 'dev': result}
        history.append(record)
        print(json.dumps({'config': output.name, **record}), flush=True)
        quality = development_quality(result, calibration)
        if best is None or quality > best:
            best = quality
            chosen_epoch = epoch + 1
            chosen_calibration = calibration
            model.save_pretrained(output, safe_serialization=True)
            tokenizer.save_pretrained(output)
    result = {'base_model': name, 'base_revision': BASE_REVISIONS[name], 'parameters': parameters,
        'max_tokens': 320, 'calibration': chosen_calibration, 'chosen_epoch': chosen_epoch,
        'history': history, 'positive_weight': ratio, 'label_smoothing': 0.02,
        'ranking_weight': ranking_weight, 'seed': args.seed, 'learning_rate': learning_rate,
        'initial_checkpoint': str(initial) if initial else None, 'initial_weights_sha256': initial_digest,
        'data_split_sha256': args.split_sha256,
        'checkpoint_selection': 'dev_recall_at_90_percent_precision_then_average_precision',
        'production_ready': False, 'activation_blockers': ['Independent human review',
            'Full production pipeline parity and concurrent VPS load evaluation'],
        'label_source': 'description_teacher_v2_plus_grounded_agreement_v3',
        'weights_sha256': hashlib.sha256((output / 'model.safetensors').read_bytes()).hexdigest()}
    result['version'] = result['weights_sha256'][:16]
    (output / 'remoteimpact_model.json').write_text(json.dumps(result, indent=2) + '\n')
    del optimizer, model
    if args.device == 'mps':
        torch.mps.empty_cache()
    return result, best


def evaluate(args, selected, destination, manifest):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    from .experiment import metrics, predict
    from .better_data import read_jsonl, write_jsonl
    from .compare import breakdown, provider_metrics
    directory = Path(args.data)
    report = {'dataset': manifest, 'selection': json.loads(selected.read_text()),
              'production_ready': False, 'models': {}}
    chosen = json.loads(selected.read_text())['chosen_model']
    for key, model_path in [('v2_balanced', args.v2), ('v3_selected', str(destination / chosen))]:
        model_path = Path(model_path)
        card = json.loads((model_path / 'remoteimpact_model.json').read_text())
        if hashlib.sha256((model_path / 'model.safetensors').read_bytes()).hexdigest() != card['weights_sha256']:
            raise ValueError('Evaluation model checksum mismatch')
        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(model_path, local_files_only=True).to(args.device)
        shared_dev = read_jsonl(directory / 'dev.jsonl')
        shared_scores = predict(model, tokenizer, shared_dev, args.device)
        shared_calibration = exact_calibration(shared_dev, shared_scores)
        result = {'weights_sha256': card['weights_sha256'], 'parameters': card['parameters'],
                  'threshold': card['calibration']['threshold'], 'sets': {},
                  'shared_development_calibration': shared_calibration,
                  'sets_at_shared_development_threshold': {}}
        for split in ['dev', 'test', 'confirmation', 'fresh', 'challenge']:
            path = directory / f'{split}.jsonl' if split != 'challenge' else Path(__file__).with_name('acceptance.jsonl')
            rows = read_jsonl(path)
            scores = predict(model, tokenizer, rows, args.device)
            result['sets'][split] = metrics(rows, scores, result['threshold'])
            # Recalibrate the old model on exactly the same dev data too, to
            # separate a better decision threshold from better model weights.
            result['sets_at_shared_development_threshold'][split] = metrics(
                rows, scores, shared_calibration['threshold'])
            if split in {'test', 'fresh'}:
                result['sets'][split]['breakdown'] = breakdown(rows, scores, result['threshold'])
            if split == 'test':
                current = provider_metrics(read_jsonl('ml/data/scout-v2-work/current-tasks.jsonl'), rows,
                                           'ml/data/scout-v2-work/cache')
                covered = set(current.pop('_covered_query_ids'))
                common_rows = [row for row in rows if row['query_id'] in covered]
                common_scores = [score for row, score in zip(rows, scores) if row['query_id'] in covered]
                result['common_provider_subset'] = metrics(common_rows, common_scores, result['threshold'])
                result['common_provider_subset_at_shared_development_threshold'] = metrics(
                    common_rows, common_scores, shared_calibration['threshold'])
                report['current_deepseek_original_test'] = current
            write_jsonl(destination / f'{key}-{split}-predictions.jsonl',
                        [{**row, 'score': score} for row, score in zip(rows, scores)])
        report['models'][key] = result
        del model
        if args.device == 'mps':
            torch.mps.empty_cache()
    (destination / 'evaluation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'event': 'evaluation_complete', 'models': {
        key: {split: {name: result for name, result in value.items() if name != 'breakdown'}
              for split, value in row['sets'].items()} for key, row in report['models'].items()}}), flush=True)


def main():
    import torch
    from .better_data import read_jsonl
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='ml/data/scout-v3')
    parser.add_argument('--output', default='ml/artifacts/scout-v3')
    parser.add_argument('--v2', default='ml/artifacts/scout-v2-ranking/minilm')
    parser.add_argument('--epochs', type=int, default=4)
    parser.add_argument('--learning-rate', type=float, default=2e-5)
    parser.add_argument('--seed', type=int, default=314162)
    parser.add_argument('--device', default='mps' if torch.backends.mps.is_available() else 'cpu')
    parser.add_argument('--evaluate-only', action='store_true')
    parser.add_argument('--resume-completed', action='store_true')
    args = parser.parse_args()
    torch.set_num_threads(4)
    directory, destination = Path(args.data), Path(args.output)
    manifest = json.loads((directory / 'dataset.json').read_text())
    args.split_sha256 = {key: value['sha256'] for key, value in manifest['splits'].items()}
    for split, info in manifest['splits'].items():
        if hashlib.sha256((directory / f'{split}.jsonl').read_bytes()).hexdigest() != info['sha256']:
            raise ValueError('Dataset content differs from frozen manifest')
    destination.mkdir(parents=True, exist_ok=True)
    selection_path = destination / 'selection.json'
    if not args.evaluate_only:
        plan = manifest['evaluation_choices']
        if args.epochs != plan['epochs'] or args.learning_rate != plan['learning_rate']:
            raise ValueError('Training must match the preregistered plan')
        train, dev = [read_jsonl(directory / f'{split}.jsonl') for split in ['train', 'dev']]
        candidates = {}
        for name in plan['configs']:
            weight = 0.25 if 'pairwise_ranking' in name else 0.0
            initial = args.v2 if name.startswith('continued_') else None
            path = destination / name
            if args.resume_completed and (path / 'remoteimpact_model.json').exists():
                result = json.loads((path / 'remoteimpact_model.json').read_text())
                digest = hashlib.sha256((path / 'model.safetensors').read_bytes()).hexdigest()
                if (digest != result['weights_sha256'] or result['data_split_sha256'] != args.split_sha256
                        or result['seed'] != args.seed or result['initial_checkpoint'] != initial
                        or result['ranking_weight'] != weight
                        or result['learning_rate'] != (1e-5 if initial else args.learning_rate)
                        or len(result['history']) != args.epochs):
                    raise ValueError('Completed ablation provenance mismatch')
                quality = development_quality(result['history'][result['chosen_epoch'] - 1]['dev'], result['calibration'])
                print(json.dumps({'event': 'resumed_completed', 'config': name}), flush=True)
            else:
                result, quality = train_config(train, dev, path, weight, args, initial=initial)
            candidates[name] = {'quality': quality, 'calibration': result['calibration'],
                                'weights_sha256': result['weights_sha256']}
        chosen = max(candidates, key=lambda name: candidates[name]['quality'])
        selection_path.write_text(json.dumps({'chosen_model': chosen, 'candidates': candidates,
            'dataset_sha256': hashlib.sha256((directory / 'dataset.json').read_bytes()).hexdigest(),
            'heldout_labels_used_for_selection': False}, indent=2) + '\n')
    # Selection is on disk before reading any final evaluation labels.
    evaluate(args, selection_path, destination, manifest)


if __name__ == '__main__':
    main()
