"""Opt-in CPU relevance model. Never downloads weights or calls an external API."""
import hashlib
import json
import math
import threading
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

from jobs.services.scout_constraints import evaluate_job, plain_job_field

_lock = threading.Lock()
_loaded = None
MAX_CANDIDATES = 12
MAX_QUERY_CHARS = 1200


def job_text(job):
    # Same field order/limits as the training dataset, with existing HTML sanitization.
    return (f"Title: {plain_job_field(job, 'title')[:300]}\n"
            f"Requirements: {plain_job_field(job, 'requirements')[:900]}\n"
            f"Description: {plain_job_field(job, 'description')[:2400]}")


def load_model():
    """Preload from a local release directory, off the web request path."""
    global _loaded
    directory = Path(getattr(settings, 'SCOUT_LOCAL_MODEL_PATH', '')).resolve()
    configured = str(directory)
    if not getattr(settings, 'SCOUT_LOCAL_MODEL_PATH', '') or not directory.is_dir():
        raise ValueError('Local Scout model directory is not configured')
    with _lock:
        if _loaded is not None and _loaded['path'] == configured:
            return _loaded
        manifest = json.loads((directory / 'remoteimpact_model.json').read_text())
        if not manifest.get('production_ready') and not getattr(settings, 'SCOUT_LOCAL_ALLOW_EXPERIMENTAL', False):
            raise ValueError('Local Scout model has not passed production acceptance')
        threshold = float(manifest['calibration']['threshold'])
        if not 0 < threshold < 1 or not manifest['calibration'].get('accepted'):
            raise ValueError('Local Scout model has no accepted development threshold')
        if not 1 <= int(manifest['parameters']) <= 100_000_000:
            raise ValueError('Local Scout model exceeds parameter budget')
        weights = directory / 'model.safetensors'
        digest = hashlib.sha256(weights.read_bytes()).hexdigest()
        if digest != manifest['weights_sha256'] or digest[:16] != manifest['version']:
            raise ValueError('Local Scout model checksum mismatch')
        max_tokens = int(manifest['max_tokens'])
        if not 32 <= max_tokens <= 512:
            raise ValueError('Local Scout model context exceeds inference budget')
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        torch.set_num_threads(1)
        tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        model = AutoModelForSequenceClassification.from_pretrained(directory, local_files_only=True,
                                                                  trust_remote_code=False, use_safetensors=True).to('cpu').eval()
        if sum(parameter.numel() for parameter in model.parameters()) > 100_000_000 or model.config.num_labels != 1:
            raise ValueError('Local Scout checkpoint is not a small binary relevance model')
        _loaded = {'path': configured, 'model': model, 'tokenizer': tokenizer, 'threshold': threshold,
                   'version': manifest['version'], 'max_tokens': max_tokens,
                   'production_ready': bool(manifest.get('production_ready'))}
        return _loaded


def warm_for_batch():
    if getattr(settings, 'SCOUT_RERANK_BACKEND', 'deepseek') == 'local':
        load_model()


def score_pairs(query, texts):
    """CPU inference only after preload. Keep cold model loads out of live requests."""
    loaded = _loaded
    configured = getattr(settings, 'SCOUT_LOCAL_MODEL_PATH', '')
    if not configured or loaded is None or loaded['path'] != str(Path(configured).resolve()):
        raise ValueError('Local Scout model is cold')
    import torch

    batch = loaded['tokenizer']([query[:MAX_QUERY_CHARS]] * len(texts), [text[:4200] for text in texts],
                                padding=True, truncation=True, max_length=loaded['max_tokens'], return_tensors='pt')
    with torch.inference_mode():
        scores = torch.sigmoid(loaded['model'](**batch).logits.flatten()).tolist()
    if len(scores) != len(texts) or any(not math.isfinite(score) or not 0 <= score <= 1 for score in scores):
        raise ValueError('Local Scout model returned invalid relevance scores')
    return scores


def rerank(candidates, preferences, fallback, diagnostics, relaxed=False):
    # Local pilot cannot promote wide-pool candidates rejected by existing rules.
    fallback = [job for job in fallback if evaluate_job(job, preferences, relaxed=relaxed).allowed]
    allowed = {job.pk for job in fallback}
    pool = [job for job in candidates if job.pk in allowed and evaluate_job(job, preferences, relaxed=relaxed).allowed][:MAX_CANDIDATES]
    if not pool:
        diagnostics['reranker'] = 'local_empty'
        return []
    try:
        if _loaded is None:
            raise ValueError('Local Scout model is cold')
        loaded = _loaded
        path = getattr(settings, 'SCOUT_LOCAL_MODEL_PATH', '')
        if not path or loaded['path'] != str(Path(path).resolve()):
            raise ValueError('Local Scout model release changed')
        if not loaded['production_ready'] and not getattr(settings, 'SCOUT_LOCAL_ALLOW_EXPERIMENTAL', False):
            raise ValueError('Experimental local model is not enabled')
        texts = [job_text(job) for job in pool]
        query = preferences.get('intent_text', '')[:MAX_QUERY_CHARS]
        body = json.dumps({'version': loaded['version'], 'threshold': loaded['threshold'], 'query': query,
                           'jobs': [(job.pk, text) for job, text in zip(pool, texts)]}, sort_keys=True)
        key = 'scout-local-v1:' + hashlib.sha256(body.encode()).hexdigest()
        try:
            scores = cache.get(key)
        except Exception:
            scores = None
        if scores is None:
            scores = score_pairs(query, texts)
            try:
                cache.set(key, scores, 300)
            except Exception:
                pass
        if len(scores) != len(pool) or any(not math.isfinite(score) or not 0 <= score <= 1 for score in scores):
            raise ValueError('Invalid cached local scores')
        selected = [job for job, score in sorted(zip(pool, scores), key=lambda pair: pair[1], reverse=True)
                    if score >= loaded['threshold'] and evaluate_job(job, preferences, relaxed=relaxed).allowed]
        diagnostics.update({'reranker': 'local', 'local_model_version': loaded['version'],
                            'rerank_candidate_count': len(pool), 'rerank_selected_count': len(selected)})
        # Retain existing grounded rule reasons. A scalar relevance score is not an explanation.
        return selected
    except Exception:
        diagnostics['reranker'] = 'local_deterministic_fallback'
        return fallback
