"""Explicit opt-in fast API fallback. Never used by default or during import."""
import hashlib
import json
import os
from pathlib import Path
import time
import requests
from ml.remote_policy.core import Decision, clean
from ml.remote_policy.llm import SYSTEM, PROMPT_VERSION, decode_decision, source_payload


class DeepSeekLLM:
    def __init__(self, api_key=None, model='deepseek-flash', cache_dir='ml/data/remote-policy/deepseek-cache', timeout=30):
        self.api_key = api_key or os.environ.get('DEEPSEEK_API_KEY', '')
        if not self.api_key: raise ValueError('Explicit DeepSeek fallback needs DEEPSEEK_API_KEY')
        self.model = model; self.cache = Path(cache_dir); self.cache.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    def predict(self, text, candidate, *, location=''):
        text = clean(text)
        if len(text) > 16000:
            return Decision(reasons=['API input budget exceeded.'], backend=self.model), {'ms': 0, 'cached': False}
        segments, payload = source_payload(text, candidate, location)
        key = hashlib.sha256(json.dumps(['deepseek', PROMPT_VERSION, SYSTEM, self.model, payload], sort_keys=True).encode()).hexdigest()
        target = self.cache / f'{key}.json'; cached = target.exists()
        if cached: record = json.loads(target.read_text())
        else:
            start = time.perf_counter()
            response = requests.post('https://api.deepseek.com/chat/completions', headers={
                'Authorization': 'Bearer ' + self.api_key, 'Content-Type': 'application/json'}, json={
                'model': self.model, 'messages': [{'role': 'system', 'content': SYSTEM},
                    {'role': 'user', 'content': json.dumps(payload)}], 'response_format': {'type': 'json_object'},
                'thinking': {'type': 'disabled'}, 'temperature': 0, 'max_tokens': 128}, timeout=self.timeout)
            response.raise_for_status()
            value = response.json()
            record = {'response': value, 'ms': (time.perf_counter() - start) * 1000}
            temp = target.with_suffix('.tmp'); temp.write_text(json.dumps(record)); temp.replace(target)
        decision = decode_decision(record['response']['choices'][0]['message']['content'], segments, self.model)
        return decision, {'ms': record['ms'], 'cached': cached, 'usage': record['response'].get('usage', {}),
                          'served_model': record['response'].get('model')}
