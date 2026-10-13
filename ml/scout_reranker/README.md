# Small Scout relevance models

This pilot trains **22.7M MiniLM** and **33.36M GTE** binary cross-encoders. It does
not train a general chat model. Production currently uses `thenlper/gte-small`
for 384-dimensional embeddings and DeepSeek for relevance decisions. Keep the
existing production vectors and calibration: a trained classifier is a separate
model, and equal vector dimensions do not make different embedding spaces interchangeable.

## Data and evaluation

`data.py` accepts a JSONL snapshot of **public** listing fields: `id`,
`organization_id`, `title`, `description`, `requirements`, optionally source and
category. It does not read saved Scouts, private briefs, CVs, or applicant data.
It removes email addresses and URLs from training text. Corpus snapshots and
weights stay in ignored `ml/data/` and `ml/artifacts/` directories; they are not
part of the application image or a public dataset release.

The bootstrap labels use unambiguous title families and synthetic query
paraphrases. Near-family negatives exercise distinctions such as data engineer
versus data scientist, accountant versus investment analyst, and donor fundraising
versus commercial sales. **These are weak occupational labels, not human judgments
or eligibility labels.** Do not interpret their metrics as user preference accuracy.
Organizations and exact duplicated content are grouped before splitting, then
queries and negative examples are generated within each split.

`acceptance.jsonl` contains 80 developer-authored synthetic challenge pairs in 20
scenarios. They exercise domain, responsibility and skill distinctions omitted
from the title labels. They are independent from the generator, but do not
replace independently human-reviewed real briefs.

Training chooses checkpoints and a minimum-97%-precision threshold **using only
the development split**. It evaluates the chosen checkpoint on held-out jobs and
challenge cases afterwards. The trained model reports `production_ready: false`:
a high weak-label score cannot authorize production activation.

## Reproduce

Use the project `.venv`; its sentence-transformers dependencies include PyTorch,
Transformers, NumPy and scikit-learn. Training itself uses PyTorch directly, without
additional training frameworks or paid provider requests.

```sh
.venv/bin/python -m ml.scout_reranker.data \
  --input /path/to/public-jobs.jsonl --output ml/data/scout-v1
.venv/bin/python -m ml.scout_reranker.experiment \
  --data ml/data/scout-v1 --output ml/artifacts/scout-v1 --epochs 3
```

The script benchmarks unchanged GTE and MiniLM embeddings on identical inputs,
then trains both classifiers. GTE's initial classification head is random; its
initial classifier metric is **not** the quality of production GTE embeddings.
The base MiniLM classifier already has general retrieval training. Models are
fine-tuned from pretrained weights, not trained from scratch. There are no paid
teacher calls in this experiment.

Apple Silicon training selects MPS; `--device cpu` is available. All application
inference uses CPU, one Torch thread, a maximum of 12 candidates and 320 tokens.
The experiment records parameter counts, thresholds, weights SHA256, test metrics,
and a local single-thread latency sample. Test timings on the development machine
are not predictions for the VPS.

## Application integration

Default `SCOUT_RERANK_BACKEND=deepseek` preserves the existing behavior.
`SCOUT_RERANK_BACKEND=local` uses the local candidate model; missing, cold, invalid
or failed weights fall back to existing eligible deterministic matches without
calling DeepSeek. It can filter/reorder candidates already accepted by existing
rules, but cannot introduce a wide-pool job. Geography, salary, exclusions and
other hard constraints remain deterministic. Scores are not displayed as confidence
percentages; existing grounded rule explanations survive.

The local weights must be preloaded with `load_model()` off the request path.
Gunicorn warmup and batch embedding warmup include that step only in local mode.
Weights never download automatically. Checksums, size, model shape, and calibration
are checked before loading.

For **offline QA only**, configure:

```sh
SCOUT_RERANK_BACKEND=local
SCOUT_LOCAL_MODEL_PATH=/absolute/path/to/ml/artifacts/scout-v1/minilm
SCOUT_LOCAL_ALLOW_EXPERIMENTAL=true
```

Experimental permission defaults to false, and all generated pilot manifests
remain unaccepted for production. The flag does not disable DeepSeek intent
parsing, close-match generation, writing tools, or applicant screening. For a
fully offline QA run use `DEEPSEEK_API_KEY=''` and `SCOUT_CLOSE_MATCHES=false` as well.
Do not enable those overrides in production simply because a training run finished.

## Promotion requirements

- A separately reviewed real-brief/job relevance set, with held-out employers,
  duplicates and time periods; explicit exclusions, multilingual/out-of-domain
  inputs and abstention cases must be covered.
- Compare selected-role precision/recall and irrelevant matches against the current
  system. Report per-domain results and sample counts, not just an aggregate.
- Measure warm and cold CPU latency, memory and concurrent workload on the VPS.
- Validate checkpoint packaging, cache/version isolation and deterministic fallback.
- Record acceptance evidence in the model release; leave `production_ready` false
  until it exists. Update thresholds using a development set, never the test set.

The next model task after relevance is extracting preference spans/classification
labels, with Python constructing JSON. No generative small model or new extraction
model is included in this first relevance pilot.

## First pilot results (2026-10-13)

See `evaluation-2026-10-13.json` for all counts, losses, thresholds and timings.
On identical weak-labelled held-out texts, unchanged GTE embeddings achieved
78.7% top-1 occupational retrieval versus 67.1% for unchanged MiniLM embeddings.
This is an encoder comparison with the experiment's text format, not a replay
of production's complete matching pipeline. Keep GTE in production.

The trained MiniLM classifier ranked the intended role first in all 20 synthetic
challenge groups; at its development-selected threshold it accepted 19/20
relevant roles and 2/60 negatives (90.5% precision). The GTE classifier accepted
17/20 relevant roles and 6/60 negatives (73.9% precision). On weak-labelled held-out
jobs, MiniLM precision/recall were 94.2%/96.8%, GTE 96.1%/96.1%. MiniLM is the pilot
relevance candidate; neither has met production acceptance.

Both were benchmarked in a separate VPS container with no network access, one CPU
and a 2GB memory cap. For batches of 12 full-length pairs, median CPU times were
1.826s MiniLM and 3.556s GTE. Peak process RSS was about 603–643MB, including Python,
PyTorch and allocator state, not just weights. These timings are useful deployment
measurements but do not measure simultaneous web-worker load. Startup is handled
by preload rather than by a user's request.

Dynamic INT8 was also tested on the VPS: full-length median times were 1.712s and
3.298s respectively, a modest improvement. It is not used by the runtime; a smaller
file/parameter count alone does not guarantee substantially faster inference.
`quantize.py` is an optional offline calibration/evaluation tool, not an application
setting. Its scores and engine must be evaluated before adopting it.

The trained release artifacts are in ignored `ml/artifacts/scout-v1/{minilm,gte}`
and in a private VPS pilot directory outside the app. Production activation is
not enabled. Next evaluation should use independently reviewed real relevance
judgments, including professional-domain distinctions missing from title-family
labels. A generative teacher's output would also be a weak label, not ground truth.
