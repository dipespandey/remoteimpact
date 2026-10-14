# Scout evaluation protocol

Use `manage.py scout_benchmark --inventory-snapshot /private/inventory.json --output /private/replay.jsonl` to replay at most 30 anonymized briefs, with one cold and up to two immediate warm runs. The default fixture contains 8 development cases, 8 held-out cases and 4 controls across engineering, policy, operations, communications, health, security, design, education and logistics. It was frozen before inspecting held-out results. The source was a bounded sample of 98 distinct recent briefs, supplemented with reported regressions and constructed controls; it is not a random traffic-weighted sample.

The command isolates caches, creates no Scouts or analytics events, and sends no emails. It does invoke the configured model provider. Reports contain full public listing evidence and query text; use anonymized inputs and keep output outside Git. Inventory IDs are frozen, but availability filters still apply and source contents can change. Compare content hashes for returned overlaps. A CLI process with a cold embedding model measures lexical fallback, not warmed web-vector retrieval. Record that distinction.

Run development cases first, then freeze implementation before inspecting holdout outcomes. Review actual listing duties and eligibility, not just the model's quotes. Record wrong-function recommendations, undisclosed restrictions, correctly caveated uncertainty, empty searches, review failures, repeated-run stability, median/p90/max response time, and stage timings. Do not count an empty result as proof that no useful vacancy exists. Known-positive recall requires separately sourced relevant candidates; returned-result audits alone cannot measure recall.

`python -m ml.scout_reranker.live_evaluation reports.jsonl --labels /private/labels.json` summarizes latency, coverage and optional query/job judgments. Labels must distinguish verified function relevance from unknown eligibility. Assistant source review is diagnostic evidence, not independent human ground truth or a calibrated recommendation score. Include unreviewed coverage and failed queries in reports; do not claim overall precision from a reviewed subset.

## Performance and cache behavior

Interpretations are cached for ten minutes using the exact normalized brief, model and category taxonomy, then grounded again against the original request. Invalid provider responses are not cached. Preview retrieval caches ranked IDs for at most 60 seconds, reloads them through current availability filters, and reruns eligibility. It does not cache source texts or apply to saved-delivery retrieval. New jobs and replacements may wait up to 60 seconds to enter a cached candidate pool. Cache failures fall back to retrieval.

Database iteration stops at the existing eligible-candidate limit instead of materializing the entire 2,000-row scan cap. Review outputs retain per-constraint verbatim evidence but omit duplicate quotes. Diagnostics record stage durations, safe failure types and input sizes; they do not record raw prompts or credentials. Retrieval checks the shared deadline before starting and every 25 scanned rows, reserving four seconds for review. Partial pools are not cached. Deadline-truncated empty searches are marked incomplete, not interpreted as no suitable jobs. A database operation or provider timeout can still exceed the deadline; this is not a hard end-to-end latency SLA.

## Primary work and candidate coverage

The combined DeepSeek review retrieves one shared pool of up to 400 eligible
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
