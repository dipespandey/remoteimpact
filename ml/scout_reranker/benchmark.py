"""Offline CPU benchmark; intentionally accepts pilot weights without serving them."""
import argparse
import gc
import hashlib
import json
import platform
import resource
import statistics
import time
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--repeats', type=int, default=20)
    parser.add_argument('--quantize', action='store_true')
    parser.add_argument('--models', nargs='+', choices=['minilm','gte'], default=['minilm','gte'])
    args = parser.parse_args()
    root = Path(args.root)
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    output = {'cpu_threads': 1, 'platform': platform.platform(), 'torch': torch.__version__,
              'dynamic_int8': args.quantize, 'models': {}}
    for name in args.models:
        directory = root / name
        card = json.loads((directory / 'remoteimpact_model.json').read_text())
        if hashlib.sha256((directory / 'model.safetensors').read_bytes()).hexdigest() != card['weights_sha256']:
            raise ValueError('Model weights checksum mismatch')
        started = time.perf_counter()
        tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        model = AutoModelForSequenceClassification.from_pretrained(directory, local_files_only=True,
                                                                  trust_remote_code=False, use_safetensors=True).eval()
        if args.quantize:
            model = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
        load_ms = (time.perf_counter() - started) * 1000
        measurements = {}
        for dataset in ['acceptance', 'public_sample']:
            rows = [json.loads(line) for line in (root / f'{dataset}.jsonl').read_text().split('\n') if line.strip()]
            samples = []
            for iteration in range(args.repeats + 1):
                offset = iteration * 12 % max(1, len(rows) - 12)
                batch = rows[offset:offset + 12]
                started = time.perf_counter()
                inputs = tokenizer([row['query'][:1200] for row in batch], [row['text'][:4200] for row in batch],
                                   padding=True, truncation=True, max_length=card['max_tokens'], return_tensors='pt')
                with torch.inference_mode():
                    logits = model(**inputs).logits.flatten()
                    scores = torch.sigmoid(logits).tolist()
                elapsed = (time.perf_counter() - started) * 1000
                if iteration:
                    samples.append(elapsed)
            ordered = sorted(samples)
            measurements[dataset] = {'batch_pairs': 12, 'median_ms': statistics.median(samples), 'p95_ms': ordered[max(0, int(len(ordered)*0.95)-1)], 'repeats': len(samples)}
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mb = rss / (1024 * 1024 if platform.system() == 'Darwin' else 1024)
        output['models'][name] = {'parameters': card['parameters'],
                                  'load_ms_after_imports': load_ms, 'peak_process_rss_mb': peak_mb,
                                  'weights_sha256': card['weights_sha256'], 'latency': measurements}
        print(json.dumps({'event': 'model', 'model': name, **output['models'][name]}), flush=True)
        del model, tokenizer
        gc.collect()
    filename = 'vps-benchmark-int8.json' if args.quantize else 'vps-benchmark.json'
    (root / filename).write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
