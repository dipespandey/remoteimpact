# Remote-work policy interpreter — experimental research release

Reads a posting and supplied applicant facts; returns `compatible`, `incompatible`
or `unknown`, with quotations from normalized source text. This concerns stated
remote-location conditions. It does not decide who should be hired, certify work
authorization, or infer citizenship from residence. It uses no real CVs or private
candidate records. No live Scout settings, database schema or application routes
are changed.

The strongest observed approach is **rules plus a guarded language-model
fallback**, not the custom trained classifiers or embeddings alone. Qwen3.5 4B
works locally; the existing DeepSeek Flash API is the practical VPS option. Rules handle
explicit country/region restrictions, exclusions, selected US states, numeric
residential UTC ranges, and supplied work authorization. The model can interpret
unfamiliar wording; it cannot override known contradictions or manufacture missing
applicant details. Ambiguous geographic scope is deliberately left unknown.

## Use

From the repository root, the rules-only SDK/CLI requires only Python:

```sh
python3 -m ml.remote_policy.cli --country NP \
  --text 'Remote worldwide. Applicants must be based in Canada.'
```

```python
from ml.remote_policy.core import Candidate
from ml.remote_policy.pipeline import PolicyEvaluator

result = PolicyEvaluator().evaluate(
    'Remote within the EEA.', Candidate(country='NO')
)
print(result.to_dict())
```

The offline **experimental hybrid** uses a separately installed local Ollama
model. Installing it is an explicit step, never done during inference. This run
used Ollama 0.33.3 and `qwen3.5:4b` digest
`d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`.
The downloaded Ollama package is approximately 3.3GB. Tags can change: confirm the
digest in a new run rather than assuming identical weights.

```sh
ollama pull qwen3.5:4b
.venv/bin/python -m ml.remote_policy.cli --country NP --local-llm qwen3.5:4b \
  --text 'The successful applicant can work remotely from any nation.'
```

Programmatically pass `LocalLLM('qwen3.5:4b')` from `ml.remote_policy.llm` to
`PolicyEvaluator`. Requests go only to `127.0.0.1:11434`. A missing model is an
explicit setup error; request failures retain the unknown rule decision. Source
segment IDs are checked before attaching quotations. A valid citation does not
prove that the model interpreted it correctly. CLI supports `--file`, `--location`,
`--state`, `--utc-offset`, repeated `--authorized-in`, `--known-no-authorization`
and `--employment-type`. Missing authorization differs from known lack of it.

The CLI does not currently expose the learned classifiers as a production
decision backend: comparisons show that they are weaker. Their verified weights
are available through `experiment.Classifier` for further offline research.

For the existing VPS, use the explicit API option instead of hosting Qwen:

```sh
DEEPSEEK_API_KEY=... python3 -m ml.remote_policy.cli --deepseek --country NP \
  --text 'The successful applicant can work remotely from any nation.'
```

This needs `requests` and sends only fallback inputs to DeepSeek. It is never
enabled automatically. In application code pass `DeepSeekLLM(api_key=...)` from
`ml.remote_policy.cloud` to `PolicyEvaluator`. Callers must decide which inputs
they are permitted to send to a third party. Existing candidate records are not
read by this module. Keys are neither written into the package nor logged.

The VPS has 4 CPU cores and about 7.6GiB RAM; available memory during the read-only
check was about 3.0GiB. The website already uses roughly 2.3GiB. A 3.3GB Qwen package
plus runtime/context memory is too tight there. No local model was loaded on the
VPS, and production application settings were not changed.

## Dataset selection and provenance

See `dataset_catalog.json` for seven inspected candidates and rejection reasons.
Jobicy was the best fit among those inspected, not a claim that all available
datasets have been exhausted. The pinned snapshot has **4,389 distinct jobs in
56 dated files**, spanning 20 August–14 October 2026. The source manifest records
the Hugging Face commit, file sizes and SHA256 digests.

Jobicy's own terms permit use of its compilation, normalization and metadata for
AI and software purposes with attribution. They explicitly **do not relicense
employer-written text**. The raw snapshot and extracted text remain in ignored
`ml/data/remote-policy/`; the cloud comparison sends only original synthetic
policies, never downloaded employer text. Original
employer descriptions are not bundled into a public dataset or the Git commit.
Committed public-source annotations contain only source IDs, offsets, hashes and
labels; reproducing them requires the pinned source. This is not permission to
redistribute employer text or a claim that a future commercial use is cleared.

The training set combines original synthetic policy statements with **metadata-only
weak supervision**. No full-posting label is inferred from `jobGeo`. After exact
input decontamination, there are 5,692 training pairs, 1,731 development pairs and
2,304 initial test pairs. Synthetic wording families are separated; employer
groups are separated for metadata. Identical posting/applicant inputs shared by
partitions are reserved for evaluation and removed from earlier partitions.

There are **zero independent human labels**. Synthetic examples have programmed
semantic labels, and public excerpts have assistant-reviewed labels. Source
excerpts are scored only against their quoted conditions, not an entire job's
requirements. These qualifications are essential: the resulting numbers are
diagnostic evidence, not estimated accuracy across all remote vacancies.

## Model experiments

All training and inference used the existing Apple Silicon machine (64GB RAM).
Training used no paid provider calls, cloud GPU rental or new hardware. A separate
40-request synthetic API comparison on the VPS used 11,559 input and 579 output
tokens: a conservative upper-bound cost of **$0.0041625** at the official peak
cache-miss rates checked on 15 October 2026. This is an estimate from recorded
usage, not an account invoice. Cached reruns incur no new API calls in this harness.

* Fine-tuned 22,714,371-parameter MiniLM cross-encoder, 5 epochs.
* A second MiniLM run, 12 epochs with a decaying learning rate.
* Fine-tuned 33,361,155-parameter GTE cross-encoder, 10 epochs.
* Unchanged GTE embeddings plus a multiclass logistic regression; regularization
  selected on development data.
* Local Qwen3.5 0.8B and 4B, with and without rules and missing-fact guards.

Classifier checkpoints are selected by development macro F1, never test scores.
The learned models receive applicant/posting pairs and predict the three labels.
They abstain if the source exceeds their 256-token window rather than silently
classifying a truncated posting. Weights use safetensors with a SHA256 manifest.
Every learned artifact remains `production_ready: false`. More epochs did not
reliably improve out-of-template generalization.

## Evaluation history and limits

The initial 2,304-pair test and 133-case mixed diagnostic exposed missed wording
and authorization requirements. They were subsequently used for diagnosis and
are **development evidence from that point**, not untouched test results.
An excerpt label was corrected from compatible to unknown because country-level
residence did not establish residence in a particular city. The original report
and label audit are retained locally.

A further 68-case confirmation set was frozen before its predictions: 40 original
cases and 28 new public-excerpt/applicant pairs from held-out employers. Its first
hybrid run scored 66/68, with one false compatible and one false incompatible.
Two conservative scope guards were then added. The same set is now a regression
set, **not an independent test of those guards**. Reports retain implementation
hashes and distinguish those stages; do not present the post-fix score as fresh
held-out accuracy. Aggregate results, final-state evidence and remaining failures
are recorded in `results.json` and the model card.

Subsequent scoped parsing resolves those specific mixed-country cases while
retaining unknown for unresolved scopes. Current regression scores must not be
presented as independent confirmation of these revisions. Separately, the VPS API
comparison used the 40 original synthetic cases only: rules 36/40, standalone
DeepSeek 39/40 and guarded hybrid 40/40. All 40 model calls were made for a fair
standalone comparison; real hybrid use would have called the model only for the
four unresolved cases in that sample. Median measured API duration was about
891ms with three concurrent benchmark workers. This is not a latency SLA or a
traffic-weighted estimate of the fallback rate. No customer or employer source
text was sent in that experiment.

Known limits include mixed inclusion/exclusion within one clause, city/province
conditions, incomplete regional conventions, date-dependent timezone/DST rules,
employment-specific exceptions and indirect policies referenced on other pages.
`unknown` means clarification is needed, not that the applicant is ineligible.
There is no claim of multilingual validation or novelty versus existing job
matching research. An independent human-reviewed, full-posting benchmark and
customer-specific validation are still required before production promotion.

Timings in reports concern short inputs on this development machine. They do not
measure VPS capacity or simultaneous users. Cached response times retain their
original recorded generation duration; cache hits are counted separately.

## Reproduce

Use the existing project `.venv`; optional pinned experiment requirements are in
`requirements-experiments.txt`. Training uses MPS by default; `--device cpu` is
available. Data generation is seeded; GPU retraining is not guaranteed bitwise
identical. Artifact checksums identify this particular run.

```sh
.venv/bin/python -m ml.remote_policy.data acquire
.venv/bin/python -m ml.remote_policy.data prepare
.venv/bin/python -m ml.remote_policy.challenges
.venv/bin/python -m ml.remote_policy.confirmation
.venv/bin/python -m ml.remote_policy.benchmark freeze
.venv/bin/python -m ml.remote_policy.experiment train --epochs 5 --schedule constant
.venv/bin/python -m ml.remote_policy.experiment train --epochs 12 \
  --output ml/artifacts/remote-policy/minilm-v2
.venv/bin/python -m ml.remote_policy.experiment train --epochs 10 \
  --base thenlper/gte-small --output ml/artifacts/remote-policy/gte
.venv/bin/python -m ml.remote_policy.experiment embeddings \
  --output ml/artifacts/remote-policy/embeddings
.venv/bin/python -m ml.remote_policy.benchmark dev --llm qwen3.5:4b \
  --output ml/data/remote-policy/dev-current.json
.venv/bin/python -m ml.remote_policy.benchmark full-test \
  --output ml/data/remote-policy/full-test-current.json
.venv/bin/python -m ml.remote_policy.benchmark confirmation --llm qwen3.5:4b \
  --output ml/data/remote-policy/confirmation-current.json
.venv/bin/python -m unittest ml.remote_policy.test_policy -v
```

These reproduce the **final implementation**. They do not recreate the historical
pre-fix rules. The full prior prediction files and training logs remain in the
ignored research directory; summarized results are committed without source text.

Country names in `countries.json` are an English snapshot of django-countries'
MIT-licensed country data. Region groupings are explicit conventions in `core.py`,
not definitive employer definitions; state-name support is currently partial.
