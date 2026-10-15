"""Train/evaluate local classifiers; test labels are never read during fitting."""
import argparse
import hashlib
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from ml.remote_policy.core import Candidate, LABELS, model_input
from ml.remote_policy.data import ROOT, read_jsonl

BASE = 'cross-encoder/ms-marco-MiniLM-L6-v2'
BASE_REVISION = '233902d25c440f23af6f7d6e94d2946bac0bee0a'
EMBEDDING = 'thenlper/gte-small'
EMBEDDING_REVISION = '17e1f347d17fe144873b1201da91788898c639cd'
MAX_TOKENS = 256


def metrics(rows, predictions):
    expected = [r['label'] for r in rows]
    if len(predictions) != len(expected): raise ValueError('Prediction coverage mismatch')
    matrix = confusion_matrix(expected, predictions, labels=list(LABELS)).tolist()
    incompatible = sum(x == 'incompatible' for x in predictions)
    compatible = sum(x == 'compatible' for x in predictions)
    return {'n': len(rows), 'accuracy': float(accuracy_score(expected, predictions)),
            'macro_f1': float(f1_score(expected, predictions, labels=list(LABELS), average='macro', zero_division=0)),
            'labels': list(LABELS), 'confusion_matrix': matrix,
            'false_compatible': sum(p == 'compatible' and e != 'compatible' for p, e in zip(predictions, expected)),
            'false_incompatible': sum(p == 'incompatible' and e != 'incompatible' for p, e in zip(predictions, expected)),
            'compatible_precision': sum(p == e == 'compatible' for p, e in zip(predictions, expected)) / max(1, compatible),
            'incompatible_precision': sum(p == e == 'incompatible' for p, e in zip(predictions, expected)) / max(1, incompatible),
            'abstention_rate': predictions.count('unknown') / max(1, len(rows))}


def batch_tokens(tokenizer, rows, device):
    pairs = [model_input(r['text'], Candidate(**r['candidate']), r.get('location', '')) for r in rows]
    return tokenizer([x[0] for x in pairs], [x[1] for x in pairs], padding=True, truncation='only_second',
                     max_length=MAX_TOKENS, return_tensors='pt').to(device)


class Classifier:
    def __init__(self, directory, device='cpu'):
        directory = Path(directory)
        self.manifest = json.loads((directory / 'manifest.json').read_text())
        actual = hashlib.sha256((directory / 'model.safetensors').read_bytes()).hexdigest()
        if actual != self.manifest['weights_sha256']: raise ValueError('Model checksum mismatch')
        self.tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(directory, local_files_only=True, use_safetensors=True).to(device).eval()
        self.device = device

    def probabilities(self, rows, batch_size=64):
        out = []
        with torch.inference_mode():
            for start in range(0, len(rows), batch_size):
                batch = rows[start:start + batch_size]
                p = self.model(**batch_tokens(self.tokenizer, batch, self.device)).logits.softmax(-1).cpu().numpy()
                # A truncated-away restriction must not produce an assertion.
                for i, row in enumerate(batch):
                    a, b = model_input(row['text'], Candidate(**row['candidate']), row.get('location', ''))
                    if len(self.tokenizer(a, b, truncation=False, verbose=False)['input_ids']) > MAX_TOKENS:
                        p[i] = [0, 0, 1]
                out.extend(p.tolist())
        return np.array(out)

    def predict(self, rows, threshold=0):
        p = self.probabilities(rows)
        return [LABELS[int(x.argmax())] if max(x) >= threshold else 'unknown' for x in p]


def train(root, output, epochs=4, device='mps', batch_size=64, base=BASE, schedule='linear'):
    torch.manual_seed(412); random.seed(412); torch.set_num_threads(4)
    train_rows, dev_rows = read_jsonl(root / 'train.jsonl'), read_jsonl(root / 'dev.jsonl')
    revision = BASE_REVISION if base == BASE else EMBEDDING_REVISION
    tokenizer = AutoTokenizer.from_pretrained(base, revision=revision)
    model = AutoModelForSequenceClassification.from_pretrained(base, revision=revision, num_labels=3, ignore_mismatched_sizes=True).to(device)
    model.config.id2label = dict(enumerate(LABELS)); model.config.label2id = {x: i for i, x in enumerate(LABELS)}
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5, weight_decay=.01)
    from transformers import get_linear_schedule_with_warmup
    steps = epochs * ((len(train_rows) + batch_size - 1) // batch_size)
    scheduler = get_linear_schedule_with_warmup(optimizer, max(1, steps // 20), steps) if schedule == 'linear' else None
    output.mkdir(parents=True, exist_ok=True)
    best = -1; history = []
    for epoch in range(epochs):
        shuffled = train_rows.copy(); random.Random(412 + epoch).shuffle(shuffled)
        model.train(); losses = []; start = time.perf_counter()
        for offset in range(0, len(shuffled), batch_size):
            rows = shuffled[offset:offset + batch_size]
            labels = torch.tensor([LABELS.index(r['label']) for r in rows], device=device)
            optimizer.zero_grad(set_to_none=True)
            result = model(**batch_tokens(tokenizer, rows, device), labels=labels)
            result.loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
            optimizer.step()
            if scheduler: scheduler.step()
            losses.append(result.loss.item())
        model.eval(); predicted = []
        with torch.inference_mode():
            for offset in range(0, len(dev_rows), batch_size):
                logits = model(**batch_tokens(tokenizer, dev_rows[offset:offset + batch_size], device)).logits
                predicted.extend(LABELS[x] for x in logits.argmax(-1).cpu().tolist())
        report = metrics(dev_rows, predicted)
        report.update({'epoch': epoch + 1, 'loss': float(np.mean(losses)), 'seconds': time.perf_counter() - start})
        history.append(report); print(json.dumps(report), flush=True)
        if report['macro_f1'] > best:
            best = report['macro_f1']; model.save_pretrained(output, safe_serialization=True); tokenizer.save_pretrained(output)
    manifest = {'base': base, 'base_revision': revision, 'parameters': sum(x.numel() for x in model.parameters()),
                'max_tokens': MAX_TOKENS, 'labels': list(LABELS), 'history': history, 'selection': 'highest development macro F1',
                'weights_sha256': hashlib.sha256((output / 'model.safetensors').read_bytes()).hexdigest(),
                'data_manifest': json.loads((root / 'split_manifest.json').read_text()),
                'training': {'seed': 412, 'epochs': epochs, 'batch_size': batch_size, 'learning_rate': 2e-5,
                             'schedule': schedule, 'device': device},
                'production_ready': False, 'independent_human_labels': 0}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))


def embedding_features(model, rows):
    # A fair semantic baseline: no rule outputs or gold labels in the features.
    pairs = [model_input(r['text'], Candidate(**r['candidate']), r.get('location', '')) for r in rows]
    texts = list(dict.fromkeys(t for p in pairs for t in p))
    encoded = model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
    mapping = dict(zip(texts, encoded))
    a = np.array([mapping[x[0]] for x in pairs]); b = np.array([mapping[x[1]] for x in pairs])
    return np.concatenate([a, b, np.abs(a - b), a * b], axis=1)


def embedding_fit(root, output, device):
    from sentence_transformers import SentenceTransformer
    from sklearn.linear_model import LogisticRegression
    import joblib
    encoder = SentenceTransformer(EMBEDDING, revision=EMBEDDING_REVISION, device=device)
    encoder.max_seq_length = MAX_TOKENS
    train_rows, dev_rows = read_jsonl(root / 'train.jsonl'), read_jsonl(root / 'dev.jsonl')
    x = embedding_features(encoder, train_rows); d = embedding_features(encoder, dev_rows)
    best = None
    for c in [.1, 1, 10]:
        clf = LogisticRegression(C=c, max_iter=500, class_weight='balanced', random_state=412).fit(x, [r['label'] for r in train_rows])
        score = metrics(dev_rows, clf.predict(d).tolist())
        print(json.dumps({'embedding_c': c, 'dev': score}), flush=True)
        if best is None or score['macro_f1'] > best[0]: best = score['macro_f1'], clf, c, score
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump(best[1], output / 'embedding-logreg.joblib')
    (output / 'embedding-manifest.json').write_text(json.dumps({'encoder': EMBEDDING, 'revision': EMBEDDING_REVISION,
           'C': best[2], 'development': best[3], 'production_ready': False}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('action', choices=['train', 'embeddings'])
    p.add_argument('--data', type=Path, default=ROOT); p.add_argument('--output', type=Path, default=Path('ml/artifacts/remote-policy/minilm'))
    p.add_argument('--epochs', type=int, default=4); p.add_argument('--device', default='mps'); p.add_argument('--batch-size', type=int, default=64)
    p.add_argument('--base', choices=[BASE, EMBEDDING], default=BASE)
    p.add_argument('--schedule', choices=['linear', 'constant'], default='linear')
    a = p.parse_args()
    if a.action == 'train': train(a.data, a.output, a.epochs, a.device, a.batch_size, a.base, a.schedule)
    else: embedding_fit(a.data, a.output, a.device)
