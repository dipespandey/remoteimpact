"""Bounded API comparison using ONLY original synthetic policies, no source text.

Explicit invocation makes at most --max-calls requests; never retries. Run in the
existing environment containing the provider credential; credentials stay there.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import statistics
from ml.remote_policy.cloud import DeepSeekLLM
from ml.remote_policy.core import Candidate, Decision, rules
from ml.remote_policy.pipeline import combine


def evaluate(path, output, max_calls=40):
    rows = [json.loads(x) for x in path.read_text().split('\n') if x.strip()]
    if len(rows) > max_calls or max_calls > 40: raise ValueError('API call budget exceeded')
    if any(r['source'] not in {'confirmation_original', 'release_original'} for r in rows):
        raise ValueError('Cloud benchmark accepts only original synthetic policies')
    model = DeepSeekLLM()
    def one(row):
        candidate = Candidate(**row['candidate'])
        rule = rules(row['text'], candidate)
        try:
            decision, timing = model.predict(row['text'], candidate)
            error = None
        except Exception as exc:
            decision, timing, error = Decision(), {'usage': {}, 'ms': 0}, type(exc).__name__
        hybrid = combine(rule, decision, candidate, row['text'])
        return {'id': row['id'], 'expected': row['label'], 'rule': rule.label, 'model': decision.label,
                'hybrid': hybrid.label, 'timing': timing, 'error_type': error}
    with ThreadPoolExecutor(max_workers=3) as pool: results = list(pool.map(one, rows))
    summary = {}
    for backend in ['rule', 'model', 'hybrid']:
        summary[backend] = {'n': len(results), 'correct': sum(r[backend] == r['expected'] for r in results),
                            'false_compatible': sum(r[backend] == 'compatible' and r['expected'] != 'compatible' for r in results),
                            'false_incompatible': sum(r[backend] == 'incompatible' and r['expected'] != 'incompatible' for r in results)}
    usage = {'prompt_tokens': 0, 'completion_tokens': 0}
    for r in results:
        if not r['timing'].get('cached'):
            for key in usage: usage[key] += r['timing'].get('usage', {}).get(key, 0)
    # Conservative peak cache-miss rates verified in official docs 15 Oct 2026.
    upper_cost = (usage['prompt_tokens'] * .30 + usage['completion_tokens'] * 1.20) / 1_000_000
    report = {'n': len(rows), 'input_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'scope': 'Original synthetic policies only',
              'summary': summary, 'usage': usage, 'upper_bound_usd_at_verified_rates': upper_cost,
              'pricing_source': 'https://api-docs.deepseek.com/quick_start/pricing/',
              'latency_ms_p50': statistics.median(r['timing']['ms'] for r in results), 'cases': results,
              'implementation_sha256': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                         for name in ['core.py', 'pipeline.py', 'cloud.py', 'llm.py']}}
    output.write_text(json.dumps(report, indent=2)); print(json.dumps({k: v for k, v in report.items() if k not in ['cases', 'implementation_sha256']}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--input', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--max-calls', type=int, default=40); a = p.parse_args(); evaluate(a.input, a.output, a.max_calls)
