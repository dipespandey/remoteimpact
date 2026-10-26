# Scout evaluation protocol

Use `manage.py scout_benchmark --inventory-snapshot /private/inventory.json --output /private/replay.jsonl` to replay at most 30 anonymized briefs, with one cold and up to two immediate warm runs. The default fixture contains 8 development cases, 8 held-out cases and 4 controls across engineering, policy, operations, communications, health, security, design, education and logistics. It was frozen before inspecting held-out results. The source was a bounded sample of 98 distinct recent briefs, supplemented with reported regressions and constructed controls; it is not a random traffic-weighted sample.

The command isolates caches, creates no Scouts or analytics events, and sends no emails. It does invoke the configured model provider. Reports contain full public listing evidence and query text; use anonymized inputs and keep output outside Git. Inventory IDs are frozen, but availability filters still apply and source contents can change. Compare content hashes for returned overlaps. A CLI process with a cold embedding model measures lexical fallback, not warmed web-vector retrieval. Record that distinction.

Run development cases first, then freeze implementation before inspecting holdout outcomes. Review actual listing duties and eligibility, not just the model's quotes. Record wrong-function recommendations, undisclosed restrictions, correctly caveated uncertainty, empty searches, review failures, repeated-run stability, median/p90/max response time, and stage timings. Do not count an empty result as proof that no useful vacancy exists. Known-positive recall requires separately sourced relevant candidates; returned-result audits alone cannot measure recall.

`python -m ml.scout_reranker.live_evaluation reports.jsonl --labels /private/labels.json` summarizes latency, coverage and optional query/job judgments. Labels must distinguish verified function relevance from unknown eligibility. Assistant source review is diagnostic evidence, not independent human ground truth or a calibrated recommendation score. Include unreviewed coverage and failed queries in reports; do not claim overall precision from a reviewed subset.

## Performance and cache behavior

Interpretations are cached for ten minutes using the exact normalized brief, model and category taxonomy, then grounded again against the original request. Invalid provider responses are not cached. Preview retrieval caches ranked IDs for at most 60 seconds, reloads them through current availability filters, and reruns eligibility. It does not cache source texts or apply to saved-delivery retrieval. New jobs and replacements may wait up to 60 seconds to enter a cached candidate pool. Cache failures fall back to retrieval.

Database iteration stops at the existing eligible-candidate limit instead of materializing the entire 2,000-row scan cap. Review outputs retain per-constraint verbatim evidence but omit duplicate quotes. Diagnostics record stage durations, safe failure types and input sizes; they do not record raw prompts or credentials. Retrieval checks the shared deadline before starting and every 25 scanned rows, reserving six seconds for review. Partial pools are not cached. Deadline-truncated empty searches are marked incomplete, not interpreted as no suitable jobs. A database operation or provider timeout can still exceed the deadline; this is not a hard end-to-end latency SLA.

## Primary work and candidate coverage

The combined DeepSeek review retrieves one shared pool of up to 160 eligible
candidates per branch (within the existing 2,000-row scan and deadline), then
applies strict and caveated ranking to those same source objects. This replaces
separate strict/provisional scans. It can change which candidates reach review;
monitor coverage as well as latency. Alternative-function retrieval remains a
separate bounded pass when needed. Initial planning still supplies role synonyms.

Listing HTML tags preserve word boundaries. Review evidence leads with a verbatim
role overview and stated workload allocations. An explicit finance/accounting
allocation of at least 50% disqualifies an unrelated brief, with an exception for
an explicitly requested COO opening whose title is COO. This is a narrow source
rule, not a general profession classifier. Qualification-only function quotes
are rejected. Model quotes still target 180 characters; grounded quotes up to
360 are accepted to avoid discarding valid selections for a small length overrun.

A generic Remote label is unknown worldwide eligibility, not a verified conflict.
It remains excluded from strict matches and can only appear with a location caveat
in Worth considering. Known country restrictions remain conflicts. Comparative
alternative explanations cannot invent an applicant country absent from the brief.

Final source checks also distinguish modal residence statements (for example,
“candidates can be located in the United States or Canada”) from unknown worldwide
eligibility. Illustrative worldwide country examples do not become restrictions.
Contingent openings carry a hiring-condition caveat. Product marketing/sales is
not substituted for a requested product-management profession unless explicitly
requested. These checks apply before model selections can override eligibility.

## Source-prepared comparison protocol

`scout_benchmark --warm-embeddings` loads the configured local embedding model before
measuring searches. Each report row records whether that model was warm; verify
`semantic_used` rather than assuming preload alone supplied vector retrieval.
Compare lexical-only and warmed semantic runs separately, with isolated caches and
the same inventory IDs. Availability and listing contents remain live; timing
includes source checks, database work and model waits. Warm repeat measurements
include parse, plan, retrieval and review cache benefits; they are not new-query SLAs.

The 14 October source-prepared run compares the 26 regression/constructed briefs
against the prior deployed implementation, then uses twelve further constructed
briefs frozen before final results are inspected. None is an independent human gold
set. Audit job duties, source quotes, country restrictions, job type and required
tools, including positive recommendations from the previous implementation.
More nonzero searches alone do not establish better relevance: the research query
with “contract, open to Europe” previously lost both constraints and could surface
full-time India/US roles. Those selections must count as known failures, not recall.
Record assistant judgments and unknowns separately from deterministic contradictions.

Source preparation additionally checks geography parity across a public text snapshot
and ten regions, and tests stale profiles, atomic JSON updates, live source changes,
HTML-separated phrases, domain alternatives, tool aliases and cached-review timeouts
on SQLite and an isolated PostgreSQL test database. Tests establish implementation
behavior, not overall recommendation accuracy.

## Empty-result regressions — 21 October 2026

Explicit requested role lists after an introductory sentence are preserved (up to eight functions), including Programme Assistant, Education Support and Content Writing. Listed functions are alternatives. Education Support in a list is not a compulsory education-sector restriction; explicit sector requirements remain. Saved Scouts rederive these constraints from their original brief on every match without rewriting historical results.

Nested exclusions such as “exclude jobs that exclude Pakistan” do not exclude the applicant country. Meta instructions such as “do not exclude jobs because they do not mention Pakistan” are neither exclusions nor additional country requests. Explicit excluded countries remain enforced. Stated candidate experience replaces the default entry-level years ceiling, while known seniority and unmet experience requirements remain hard rejections. Including short-term contracts in an otherwise open search does not narrow all jobs to contracts.

The combined review pool defaults to 160 eligible candidates per branch (`SCOUT_REVIEW_POOL_LIMIT`), rather than 400, preserving time for source review. This trades candidate breadth for latency and is not a recall guarantee. Unfinished preparation with no results, partial reviews, and retrieval deadline exhaustion remain visibly retryable rather than evidence of an empty market. The same-brief retry uses the loading state and does not send email.

Regression fixtures are in `jobs/tests/test_scout_empty_regressions.py`. Browser tests cover 390px/1280px retry, exact brief preservation and no email send. A read-only production replay returned possibilities for all three reported briefs, with unresolved location/seniority warnings retained. Returned possibilities do not establish Pakistan eligibility.

## October 26 requirement and recovery regressions

`jobs.tests.test_scout_audit_regressions` covers synthetic cases derived from the
production audit, plus held-out currency, range, study and negative-evidence
variants. No customer briefs or identifiers belong in fixtures.

Pay intervals retain their lower and upper amounts, currency and period. A trailing
`+` keeps the upper end open. Known comparable pay must overlap the stated interval;
unknown units or currency require a caveat rather than an invented conversion.
Reversed or mixed-currency intervals require clarification. Study arrangements do
not become requested employment types. Ambiguous “out of [country]” language is
preserved as unresolved geography, never a confirmed country preference. These
constraints are re-grounded for model parses and existing saved Scouts alike.

Matching and final warnings share source geography extraction. Original-source
country restrictions override a generic catalogue “Remote” label. When the brief
has no working country, a restricted role must carry a location warning and remain
in the provisional tier. Geography profile version 6 invalidates older prepared
profiles; final verification version 2 also annotates recovered results.

A broad request without a profession can recover a bounded source-backed browsing
option from actual duties. It remains labelled as needing role details and full
review; it does not establish personal suitability. All deterministic restrictions,
explicit prior rejections, deduplication and original-source verification still
apply. No new provider calls or prompt changes are introduced by this recovery.

Empty preview results with incomplete review receive one bounded queue retry under
the existing two-attempt limit. Genuine no-match and useful partial results are not
delayed. Exhaustion retains the incomplete-review outcome and existing retry UI,
rather than implying the inventory has no suitable role. Each attempt retains its
existing four-call and deadline budgets. Semantic model warm-up is unchanged and
requires a separate controlled relevance, memory and latency comparison.

## Product requirement: no dead-end Scout results

The owner requires Scout screens to offer probable openings even when the exact
search returns no good matches. Preview and saved-result pages must show current
exploration options after empty, failed or incomplete reviews, whenever real
publishable remote openings are available. Loading remains loading; an actually
empty inventory must never be disguised with invented, expired or non-job records.

`explore_openings` ranks related source duties first, deduplicates reposts and
excludes known physical/mandatory-office roles and non-openings. Its bounded
96-row candidate read has no model calls. Exploration cards explicitly disclose
that they are not verified matches, with source pay and location restrictions and
known differences. Strict match gates, delivery history, billing/free credits and
match email contents remain unchanged. Analytics records `explore_count` separately
from `match_count`. Regression tests cover both synchronous and asynchronous
previews, saved results, failures, safety exclusions, ranking and mobile rendering.
