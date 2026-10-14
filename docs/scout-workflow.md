# Bounded Scout workflow

Previews interpret the brief with the same grounded parser used for saved Scouts.
Saved Scouts reuse their stored interpretation, re-grounded against the original brief.

The matching pipeline plans up to six retrieval phrases, retrieves and applies the
original deterministic eligibility and title gates, and makes at most one refinement
when fewer than three requested matches survive. Planning can change retrieval wording
only; it cannot modify preferences. Candidates are then reviewed and ordered using the
existing quote-validated evidence reranker. Existing provisional and close matches stay
labelled, including unverified constraints.

A context-local budget is shared across parsing, planning, refinement, reranking and
close-match selection: at most four provider calls, zero SDK retries, and an 18-second
deadline. Each call is limited to the remaining time. This bounds model waits, not the
entire HTTP request: database retrieval and rendering take additional time. Cached
plans and evidence decisions do not consume calls. Errors or exhausted budgets use
existing deterministic fallbacks. No browser tools, autonomous actions or emails are
part of planning. `SCOUT_MULTISTEP_ENABLED=False` disables planning/refinement.

Preview event metadata records workflow version, provider-call count, retrieval passes,
and stage/fallback diagnostics for operational review. The current provider remains
DeepSeek; this change does not activate a locally trained model.

## Two tiers and inventory coverage — 14 October 2026

First-time searches use the whole currently available inventory, including older
postings still admitted by the public source-freshness/expiry policy. Recurring
Scouts retain the last-send posting cutoff and delivered/negative-feedback exclusions.

Good matches are separated from Worth considering. Unverified constraints and
model-reviewed adjacent roles are in the latter, with the caveat/difference visible.
A persisted reason marker keeps that classification intact when restoring delivery
history. Previews, saved results and email HTML/text use the same grouping function.
Good matches are not a guarantee of employment eligibility or a calibrated score.

Planning leaves two provider calls for relevance review and alternatives. A relaxed
rerank leaves the final call for close-match evidence review. Original required
skills, location, salary, job type, seniority and unresolved constraints remain
protected; sector/wording differences can only appear as labelled alternatives.
Known-ineligible or unrelated jobs are not inserted to achieve a nonzero count.

## Source evidence and constraint review — 14 October 2026

Reviews now sample duties, work arrangement, residence restrictions, qualifications
and compensation throughout the full stored listing. Identical description and
requirements fields are sent once. Excerpts remain verbatim; this is bounded
selection, not an assertion that every requirement has been extracted.

Provider selections require separate quoted checks for function and every requested
remote, location, seniority, skill and salary dimension. Missing/forged checks,
explicit conflicts and unconfirmed job function cannot qualify. An office address
alone cannot confirm hiring eligibility; "remote setting" cannot confirm fully
remote employment. Mandatory hybrid/on-site arrangements are rejected for remote
briefs, while optional office arrangements remain reviewable. Policy alternatives
need policy-function evidence, not just a climate-sector company description.

Unconfirmed dimensions remain visible in Worth considering. Geographic residence
restrictions are displayed even when compatible with a broader requested region.
Older-than-180-day postings receive a confirmation warning; feed sightings do not
reset posting age or prove employer availability. This is a caution, not a closure
verdict or a replacement for the existing availability checks.

Results with fewer outstanding checks rank first within the second tier, before the
final display limit is applied. UI fit numbers have been removed because they were
uncalibrated ranking scores. Provider review failures withhold unreviewed results;
an incomplete preview explicitly asks the seeker to retry rather than asserting
that no suitable jobs exist. No automatic relaxation changes the seeker's brief.

The four-call/18-second workflow budget remains. Strict and provisional candidates
share one provider review, leaving time to judge alternatives; its timeout is at
most 10 seconds and is further capped by the remaining workflow time. Strict evidence review is capped
at 12 candidates and 44,000 prompt characters; alternatives now review at most 12
candidates, with full-source excerpts instead of 30 introductions. This tradeoff
needs recall monitoring. The offline training comparison now scopes its comparison
to the current function-review layer; it does not measure end-to-end eligibility.

Validation includes adversarial regression cases and read-only production-inventory
runs of the two reported briefs. These are development checks, not a blinded or
representative relevance benchmark. Further evaluation should independently label
real search/candidate pairs and track hard-constraint violations, function precision,
useful alternative coverage, known-match recall, run stability and latency. Do not
claim general precision from passing software tests or these two searches.

## Broader evaluation and performance — 14 October 2026

See [the reproducible evaluation protocol](scout-evaluation.md). A frozen 20-brief
suite expands beyond the two original searches. Development and held-out outcomes
are recorded separately; once a held-out failure informs a fix, it becomes a
regression case and is no longer claimed as untouched validation. Source-reviewed
failures include paid-versus-volunteer work, currency denominations mistaken for
geography, and eligibility restrictions preserved only in titles. Explicitly
guaranteed pay cannot be satisfied by an undisclosed salary in either tier.

Preview candidate-ID caching rechecks live availability and source eligibility;
interpretation caching re-grounds every request. Streaming retrieval preserves
existing scan/eligibility limits. Per-stage timings and safe review-failure types
support operational diagnosis. Reduced review JSON keeps the same source-grounding
and complete-candidate checks. None of these measures establish general recall or
a blanket quality guarantee, and the model-call deadline is not a total latency SLA.
