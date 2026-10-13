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

## Description-grounded v2 (2026-10-13)

`better_data.py` partitions the public snapshot before sampling balanced anchors,
generating synthetic briefs, and mining GTE neighbours. A separate Pro teacher
judges the candidates without seeing their anchor identity. Labels require an
exact supporting quotation from the **student-visible 320-token evidence**;
uncertain or unsupported pairs are discarded. The title taxonomy balances
sampling only; it does not assign these new labels. Training includes a bounded
weak replay set at one-quarter loss weight.

Final training has 3,716 pairs: 2,295 grounded teacher labels and 1,421 weak replay
pairs. Development/test have 336/394 teacher-labeled pairs. Those two files stayed
byte-identical when training expanded. A final confirmation set has 151 pairs
from 26 new synthetic briefs and employers absent from every earlier candidate
set. This remains teacher evaluation, with **zero independent human judgments**.
The original test was observed during exploration; confirmation labels were not
used to tune checkpoints, thresholds or prompts. There is no chronological or
real-user holdout in this snapshot.

Two 22.7M checkpoints retain the precision/recall tradeoff. On the 372-pair subset
where the existing provider produced valid decisions:

| Model / decision policy | Precision | Recall |
| --- | ---: | ---: |
| v1, original threshold | 43.6% | 89.0% |
| v2, ranking checkpoint / development 90% target | 83.6% | 56.0% |
| v2, strict checkpoint / development 97% target | 100% | 17.0% |
| Current DeepSeek relevance prompt | 83.6% | 97.0% |

The comparison uses fixed public candidates and the current production prompt
and quote validator. Three invalid provider groups are excluded from **every**
model in that table. It does not replay eligibility, retrieval, six-second timeouts
or deterministic fallback. It does not establish that v2 can replace DeepSeek.
The strict v2 ranks a positive first in 92.3% of positive-containing original test
groups versus 84.6% for v1. On fresh confirmation, the ranking checkpoint makes
zero false selections among 17 selected roles but misses 18 of 35 positives.
Report counts alongside rates: these small samples are not production accuracy.

`evaluation-v2-2026-10-13.json` records all operating points, domain sample counts,
checksums, source limitations and isolated VPS benchmarks. Twelve long pairs take
about 1.7–1.9 seconds on one VPS CPU; process RSS is about 605MB. The new data
improves relevance distinctions; it does not change model architecture or solve
concurrent-serving latency. Both manifests retain `production_ready: false`.

The 779 offline teacher calls stayed below a $5 reservation ceiling. The conservative
peak/uncached token-cost estimate is $2.49; actual billing can be lower. Rates were
checked against [DeepSeek's official pricing](https://api-docs.deepseek.com/quick_start/pricing/).
`teacher.py` reserves worst-case cost before dispatch, persists usage, reuses
content-addressed responses, and retains reservations for interrupted attempts.
Credentials are supplied only to the isolated teacher process. It has no database
or web-serving access and logs counters rather than input or provider errors.

To reproduce, run from the repository root. Teacher stages require a private API
key environment; never put it in task files or commit datasets/cache responses.

```sh
.venv/bin/python -m ml.scout_reranker.better_data prepare --train-anchors-per-family 12
# Run teacher.py on generate-tasks.jsonl; use the same cache and ledger for all stages.
.venv/bin/python -m ml.scout_reranker.better_data mine
# Run teacher.py on label-tasks.jsonl, then:
.venv/bin/python -m ml.scout_reranker.better_data finalize
.venv/bin/python -m ml.scout_reranker.experiment --data ml/data/scout-v2 \
  --output ml/artifacts/scout-v2-expanded --bases minilm \
  --init-checkpoint ml/artifacts/scout-v1/minilm --skip-embedding-baselines \
  --epochs 6 --learning-rate 0.00002
.venv/bin/python -m ml.scout_reranker.experiment --data ml/data/scout-v2 \
  --output ml/artifacts/scout-v2-ranking --bases minilm \
  --init-checkpoint ml/artifacts/scout-v1/minilm --skip-embedding-baselines \
  --epochs 6 --learning-rate 0.00002 --selection-metric average_precision --min-precision 0.90
.venv/bin/python -m ml.scout_reranker.compare prepare-current
# Run teacher.py on current-tasks.jsonl for the provider comparison.
# Freeze training choices before preparing/labeling the confirmation stages.
.venv/bin/python -m ml.scout_reranker.confirmation prepare
.venv/bin/python -m ml.scout_reranker.confirmation mine
.venv/bin/python -m ml.scout_reranker.confirmation finalize
.venv/bin/python -m ml.scout_reranker.compare evaluate \
  --v2 ml/artifacts/scout-v2-ranking/minilm --strict-v2 ml/artifacts/scout-v2-expanded/minilm
.venv/bin/python -m ml.scout_reranker.review
```

`review.html` is a standalone blind worksheet with keyboard judgments, local
progress and JSONL export. It exposes neither teacher labels nor model scores.
Use the exported human labels for a subsequent independent evaluation; generating
the worksheet does not constitute review. Model cards, data and weights are in
ignored local artifact directories and the private VPS pilot directory. The live
production model configuration is unchanged.

## Curated v3 curriculum (2026-10-13)

`curation.py` adds short broad searches, transferable-function requests and precise
exclusions. New anchors are sampled within the existing employer partitions;
no private saved searches or CVs are read. Short broad requests have a median of
10 words, compared with 46 words in the preceding teacher training set. A Pro
teacher labels the student-visible candidate text; a separately prompted Flash
teacher reviews every new training candidate without seeing the first judgment.
Only grounded agreements enter training. Both teachers are related DeepSeek
models, so agreement is a consistency filter, not independent human validation.

Training contains 3,208 pairs: the existing 2,295 description-grounded teacher
pairs plus 913 new agreements. Title-family rehearsal labels are removed. The
curation pass rejects unsupported generation quotes, uncertain judgments and
disputed decisions. A five-word-shingle Jaccard audit removes likely training
reposts of evaluation text at similarity >= 0.85; none occurred in this snapshot.
Development expands to 459 pairs. The old 394-pair test and 151-pair confirmation
files stay byte-identical and are explicitly regression sets now. A fresh final
set has 185 pairs from previously unused employers. The remaining source pool
contains only jobs outside the old title taxonomy, which limits its coverage.

`curriculum_training.py` keeps all candidates for a query in one batch, balances
the positive loss using training-only class counts, applies mild label smoothing,
and compares binary classification with classification plus pairwise ranking.
Its threshold sweep uses actual development scores and keeps score ties together;
the previous fixed grid could skip useful operating points. Each batch averages
loss over query groups. Ranking is an additional objective, not a substitute for
training no-match cases. The approach follows the distinctions in the official
[CrossEncoder loss documentation](https://www.sbert.net/docs/package_reference/cross_encoder/losses.html).

The initial plan compared two foundation-initialized variants. Their early
development recall fell, so a recorded **development-only amendment**, made
before final evaluation, adds the same two variants continued from the verified
v2 ranking checkpoint at half the learning rate. Those continued branches retain
v1 ancestry; removing title labels from this round does not erase earlier training.
The original completed foundation BCE run is reused after provenance verification.
Checkpoint and variant selection use development recall at 90% observed precision,
then average precision. These point estimates on a small development set are not
an accuracy guarantee. Final labels do not select the winning variant or threshold.

The evaluation also recalibrates the old v2 model on exactly the same development
set, separating weight improvements from threshold changes. Full production
retrieval, eligibility, provider timeouts and fallback still require separate
validation. Both releases retain `production_ready: false`.

```sh
.venv/bin/python -m ml.scout_reranker.curation prepare
# teacher.py: generate-tasks.jsonl, shared content cache and bounded usage ledger
.venv/bin/python -m ml.scout_reranker.curation mine
# teacher.py: all label-tasks.jsonl AND review-tasks.jsonl, same cache/ledger
.venv/bin/python -m ml.scout_reranker.curation finalize
.venv/bin/python -m ml.scout_reranker.curriculum_training
```

For an amended reproduction, retain `scout-v3-work/plan.json` and the resulting
dataset manifest supplied with the release; a fresh `prepare` creates the original
two-variant plan. The saved amendment records why and when the candidate list grew.
Data, models, provider responses and blind review worksheets remain ignored private
artifacts. Paid labeling runs in an isolated process under a $5 reservation ceiling.

The final comparison is **mixed**, not an overall replacement win. Selected v3
improves original-test positive top-1 ranking from 90.4% to 94.2%, but at its frozen
threshold recalls only 40.6% of positives. On the fresh 185-pair check, v3 accepts
20/40 positives and zero negatives; the previous v2 frozen policy accepts 28/40
positives and one negative. At the same newly expanded development calibration
target, old v2 accepts 19/40 positives with zero negatives: the matched-policy
gain is just one additional positive. Original-test current-provider relevance
recall is still 97% on its shared valid subset, versus v3's 42%. Production stays
on GTE + DeepSeek. See `evaluation-v3-2026-10-13.json` for full counts, alternative
initializations, calibration comparison and limitations. The 419 teacher calls
cost at most $1.17 at conservative peak/uncached rates; this is not an invoice.
