# Remote policy hybrid, 15 October 2026

**Experimental. Selected architecture: rules + guarded language-model fallback.**
Use local Qwen3.5 4B for offline experiments, or explicit DeepSeek Flash API calls
for a VPS-compatible fallback. The custom trained models did not win.

## Intended input/output

Input: a posting, optional location metadata, and supplied country, US state,
residential UTC offset, work-authorized countries and contract type. Output:
compatible, incompatible or unknown under the stated location conditions, plus
source quotations. Missing facts remain missing. This is not a hiring score or
verification of legal work authorization. City/province conditions, policy links,
complex exceptions and some regional definitions remain outside reliable coverage.

## Data

Selected from seven inspected public dataset candidates: first-party
[Jobicy remote jobs](https://huggingface.co/datasets/jobicy/remote-jobs), pinned
revision `38b2307f0dc34786a46041c0c4455b61bbc1a8dd`.
4,389 jobs / 56 files. Raw descriptions stay in the ignored research directory;
Jobicy's terms do not relicense employer text. Training uses original synthetic
policies and metadata-derived weak labels, not inferred full-posting gold.

After cross-partition input decontamination: 5,692 training, 1,731 development and
2,304 initial test pairs. Most pairs are synthetic. Employers and wording families
are separated across partitions. Metadata duplicates within a split can still
share the same policy; pair counts are not independent observations.

There are **zero independent human labels**. Public excerpt judgments and original
challenge labels are assistant-authored. No real applicant records are used.

## Measurements and interpretation

The first additional 68-case confirmation run, frozen before its predictions,
produced **66 correct hybrid decisions**, one false compatible and one false
incompatible. It contains 40 original cases and 28 public-excerpt/applicant pairs.
It was subsequently used to improve scoped country parsing. Its current 68/68
passing regression result is **not independent accuracy evidence**.

On that same 68-case material, unchanged learned-model results were:

| Approach | Correct |
| --- | ---: |
| 22.7M MiniLM classifier, first run | 45/68 |
| 22.7M MiniLM classifier, second run | 45/68 |
| 33.4M GTE classifier | 37/68 |
| GTE embeddings + logistic regression | 38/68 |
| Standalone local Qwen3.5 4B | 55/68 |

These results argue against shipping the fine-tuned classifiers. They do not show
that rules alone solve unrestricted job-policy language. The initial held-out test
exposed dangerous missed authorization wording; those initial results and the
subsequent corrections remain recorded in `results.json`.

A separate bounded VPS comparison sent only the 40 original synthetic policies to
DeepSeek Flash: rules 36/40, standalone model 39/40, hybrid 40/40. This reused known
synthetic cases and is a feasibility comparison, not a new independent benchmark.
It used 11,559 prompt and 579 completion tokens; the conservative estimated cost
at verified peak cache-miss rates is $0.0041625. Median API duration was 891ms with
three concurrent benchmark workers. A real hybrid would have called the API for
four of those cases; this is not an estimated production fallback rate.

## Compute and artifacts

Training used Apple Silicon/MPS with 64GB RAM; CPU inference was also measured.
The VPS has 4 CPU cores, about 7.6GiB RAM and roughly 3GiB available during the
check. Hosting the 3.3GB Qwen package plus runtime alongside the website is too
tight. The API option avoids that memory requirement. No weights were activated
in the live Scout and no production application settings were changed.

Learned safetensors checkpoints and checksum manifests are stored locally under
`ml/artifacts/remote-policy/{minilm,minilm-v2,gte}`. The release choice is recorded
in `ml/artifacts/remote-policy/release.json`. Reproduction commands, source hashes,
base-model revisions and metric definitions are in `README.md` and `results.json`.
Every artifact remains `production_ready: false`.

Before production promotion: obtain independent human review of full postings and
counterfactual applicant facts, validate rights for the intended data use, and run
customer-specific coverage, concurrency and latency checks. Benchmark results do
not establish commercial demand or novelty.
