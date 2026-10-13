"""Evaluate/calibrate dynamic INT8 on development data, then inspect held-out results."""
import argparse
import json
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from ml.scout_reranker.experiment import calibrate, cpu_latency, metrics, predict, read_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--models', required=True)
    args = parser.parse_args()
    torch.set_num_threads(1)
    if torch.backends.quantized.engine == 'none':
        torch.backends.quantized.engine = next(engine for engine in ['fbgemm', 'x86', 'onednn', 'qnnpack']
                                               if engine in torch.backends.quantized.supported_engines)
    root = Path(args.models)
    dev, test = [read_rows(Path(args.data) / f'{split}.jsonl') for split in ['dev', 'test']]
    golden = read_rows(Path(__file__).with_name('acceptance.jsonl'))
    report = {}
    for name in ['minilm', 'gte']:
        directory = root / name
        card = json.loads((directory / 'remoteimpact_model.json').read_text())
        model = AutoModelForSequenceClassification.from_pretrained(directory).eval()
        tokenizer = AutoTokenizer.from_pretrained(directory)
        model = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
        calibration = calibrate(dev, predict(model, tokenizer, dev, 'cpu'))
        scores = predict(model, tokenizer, test, 'cpu')
        golden_scores = predict(model, tokenizer, golden, 'cpu')
        result = {'engine': torch.backends.quantized.engine, 'calibration': calibration,
                  'test': metrics(test, scores, calibration['threshold']),
                  'golden': metrics(golden, golden_scores, calibration['threshold']),
                  'cpu_latency': cpu_latency(model, tokenizer, golden),
                  'scope': 'Local development CPU; recheck numerical behavior on deployment CPU'}
        card['cpu_int8'] = result
        (directory / 'remoteimpact_model.json').write_text(json.dumps(card, indent=2) + '\n')
        report[name] = result
        print(json.dumps({'model': name, **result}), flush=True)
    (root / 'quantization.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
