"""Bounded, resumable offline labeling of public data. No Django or web imports."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

# Conservative peak, uncached USD / million tokens, checked 2026-10-13.
# https://api-docs.deepseek.com/quick_start/pricing/
RATES = {'deepseek-flash': (0.30, 1.20), 'deepseek-v4-pro': (1.32, 3.96)}


def task_id(task):
    return hashlib.sha256(json.dumps(task, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Reserve worst-case cost before dispatch; unfinished attempts remain charged."""
    def __init__(self, path, limit):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.limit = limit
        self.lock = threading.RLock()
        self.attempts = {}
        if self.path.exists():
            for line in self.path.read_text().split('\n'):
                if line.strip():
                    event = json.loads(line)
                    self.attempts[event['attempt_id']] = event['cost_upper_usd']

    @property
    def spent(self):
        with self.lock:
            return sum(self.attempts.values())

    def _record(self, event):
        with self.path.open('a') as handle:
            handle.write(json.dumps(event) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        self.attempts[event['attempt_id']] = event['cost_upper_usd']

    def reserve(self, task, max_tokens):
        rate_in, rate_out = RATES[task['model']]
        # UTF-8 bytes bound text tokens conservatively; allow protocol overhead.
        tokens = len((task['system'] + json.dumps(task['payload'], ensure_ascii=False)).encode()) + 256
        cost = (tokens * rate_in + max_tokens * rate_out) / 1_000_000
        with self.lock:
            if self.spent + cost > self.limit:
                raise BudgetExceeded('Offline teacher budget exhausted')
            attempt = uuid.uuid4().hex
            self._record({'attempt_id': attempt, 'state': 'reserved', 'task_id': task_id(task),
                          'cost_upper_usd': cost, 'estimated_input_token_ceiling': tokens,
                          'max_output_tokens': max_tokens})
        return attempt

    def finish(self, attempt, task, usage):
        rate_in, rate_out = RATES[task['model']]
        cost = (usage.prompt_tokens * rate_in + usage.completion_tokens * rate_out) / 1_000_000
        with self.lock:
            self._record({'attempt_id': attempt, 'state': 'finished', 'task_id': task_id(task),
                          'cost_upper_usd': cost, 'prompt_tokens': usage.prompt_tokens,
                          'completion_tokens': usage.completion_tokens})


def run_task(task, cache_dir, budget):
    """Never log input, provider error text, credentials, or personal data."""
    identifier = task_id(task)
    path = Path(cache_dir) / f'{identifier}.json'
    if path.exists():
        return 'cached'
    from openai import OpenAI
    max_tokens = int(task.get('max_tokens', 1400))
    attempt = budget.reserve(task, max_tokens)
    started = time.monotonic()
    client = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url='https://api.deepseek.com',
                    timeout=55, max_retries=0)
    response = client.chat.completions.create(
        model=task['model'], messages=[{'role': 'system', 'content': task['system']},
            {'role': 'user', 'content': json.dumps(task['payload'], ensure_ascii=False)}],
        max_tokens=max_tokens, temperature=0, response_format={'type': 'json_object'},
        extra_body={'thinking': {'type': 'disabled'}})
    if response.usage is None:
        raise ValueError('Provider usage is missing; reservation remains charged')
    budget.finish(attempt, task, response.usage)
    if not response.choices or response.choices[0].finish_reason != 'stop':
        raise ValueError('Incomplete teacher response')
    output = json.loads(response.choices[0].message.content or '')
    result = {'task_id': identifier, 'requested_model': task['model'], 'served_model': response.model,
              'output': output, 'seconds': round(time.monotonic()-started, 3),
              'usage': {'prompt_tokens': response.usage.prompt_tokens,
                        'completion_tokens': response.usage.completion_tokens}}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False) + '\n')
    temporary.replace(path)
    return 'completed'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks', required=True)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--budget-usd', type=float, default=5)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    if not 0 < args.budget_usd <= 5 or not 1 <= args.workers <= 8:
        raise ValueError('Pilot exceeds bounded cost/concurrency')
    if not os.environ.get('DEEPSEEK_API_KEY'):
        raise ValueError('Teacher API key is unavailable')
    tasks = [json.loads(line) for line in Path(args.tasks).read_text().split('\n') if line.strip()]
    if len({task_id(task) for task in tasks}) != len(tasks):
        raise ValueError('Duplicate tasks would charge twice')
    budget = Budget(args.ledger, args.budget_usd)
    counts = {'completed': 0, 'cached': 0, 'failed': 0, 'budget_skipped': 0}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_task, task, args.cache, budget) for task in tasks]
        for index, future in enumerate(as_completed(futures), 1):
            try:
                counts[future.result()] += 1
            except BudgetExceeded:
                counts['budget_skipped'] += 1
            except Exception as error:
                counts['failed'] += 1
                print(json.dumps({'event': 'teacher_failed', 'error_type': type(error).__name__}), flush=True)
            if index % 20 == 0 or index == len(tasks):
                print(json.dumps({'event': 'teacher_progress', 'finished': index, 'tasks': len(tasks),
                                  'cost_upper_usd': round(budget.spent, 4), **counts}), flush=True)
    print(json.dumps({'event': 'teacher_done', 'cost_upper_usd': round(budget.spent, 4), **counts}), flush=True)


if __name__ == '__main__':
    main()
