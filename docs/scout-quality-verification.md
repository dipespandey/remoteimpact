# Scout evidence verification

The October 25 audit found courses treated as employment, physical jobs marked
remote, optional employer/sector preferences enforced as mandatory, and hourly
source pay ignored when annual salary columns were empty.

The bounded workflow is interpret → retrieve → review → verify. Existing model
calls propose search wording and per-constraint evidence; deterministic source
checks decide which proposals can be displayed or delivered. No additional
provider calls, autonomous browsing, email sends or historical-result rewrites
are introduced by this change.

| AI boundary | Contract |
| --- | --- |
| Purpose | Review requested job functions and uncertain constraints |
| Tier/access | Existing configured server AI client through `scout_workflow.generate`; no new call or provider mapping |
| Trust | Briefs, listings, extracted facts and model responses are untrusted data |
| Output | Existing candidate-ID and per-constraint quote schema; verification adds no model output fields |
| Verification | Concrete opening, platform remote policy, explicit requirements, source compensation units and requested weekly hours are checked locally before and after review |
| Evidence | Source-backed facts and exact differences; unknown facts remain caveated possibilities, never verified suitability |
| Failure | Known contradictions are rejected even after a positive model judgment. An unfinished review remains retryable. Existing deadline and four-call limit remain |
| Evaluation | Synthetic regressions for audited failure shapes, positive/negative controls, adversarial model acceptance, saved/preview parity, and unchanged historical data |

Sector and employer alternatives stay alternatives. Preferred sectors contribute
ranking signals rather than SQL exclusions. A narrow request such as "climate
engineering only" retains its sector requirement.

The bounded review pool and final selections load original stored source prose
with a projected batch query. When available, it replaces enriched descriptions
and requirements for review; neither the catalogue record nor the delivery ledger
is rewritten. Confirmed review quotes must occur in those original fields. Source
edits invalidate cached preview fingerprints.

Original hourly/monthly compensation keeps its source amount and unit. Monthly
and annual rates can be compared exactly; hourly rates cannot establish annual
earnings without an agreed working schedule. Missing annual fields do not mean
that a source hourly rate was undisclosed. Working-hour targets are surfaced as
differences; explicit limits remain rejection gates.

Model relevance judgments are not accuracy measurements. Regression tests prove
these implementation boundaries, not general precision or recall. Production
rollout uses a reviewed PR and the exact CI-checked main commit.
