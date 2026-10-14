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
