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
