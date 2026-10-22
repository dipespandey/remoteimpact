# Scout loading and retrieval optimization — 2026-10-16

Status: implemented locally, not deployed.

Scout now shows an animated loading ring and “Finding matches…” immediately on homepage sample links, the homepage composer submit, preview editing, and final Scout creation. A live status message explains that matching and eligibility checks are running. Native form validation runs before loading starts. Repeat clicks are suppressed; history restoration restores controls. Reduced-motion users get a static ring with the same status text.

The backend changes preserve matching policy and AI prompts:

- Lexical retrieval ranks IDs before fetching long source fields in batches of 100. It retains ordering, the 2,000-row scan cap and the eligible-candidate limit. Hydration uses the filtered availability queryset, so rows removed between reads are omitted. Semantic retrieval retains its original SQL shape; changing its projection caused a candidate-set difference in an initial experiment, so that variant was discarded.
- Source profiles defer responsibility-section extraction until duty evidence is requested. Geography and workplace checks continue to compute the evidence they require. The persistence command still builds complete profiles with the same version, source hash and offsets.
- Within one workflow, identical source text can reuse its profile. The key includes the analysis version and exact source hash; source mutations trigger new analysis. The bounded memo is discarded at the end of the search. Cross-request caches and user delivery history are unaffected.

Environment switches (all default `true`): `SCOUT_ID_FIRST_RETRIEVAL`, `SCOUT_LAZY_DUTY_SPANS`, `SCOUT_REQUEST_PROFILE_CACHE`. Set any to `false` to disable that optimization independently.

## Validation and limits

The read-only local PostgreSQL snapshot contains 29,062 jobs. Six historical production brief preferences were compared using alternating baseline/optimized/optimized/baseline runs, two runs per variant. A fixed stored embedding supplied the semantic query for both variants; no embedding model or provider was called. All six comparisons returned identical ordered candidate IDs. Retrieval fell 12.1–34.5% per search; aggregate time fell 24.9%, with median retrieval time 1.008s → 0.801s.

These numbers measure local retrieval, not live end-to-end latency. The snapshot predates a schema-only indexing field, which was deferred in both variants. Already prepared source profiles avoid some of this analysis today, so their live improvement may be smaller. Live AI review and network waits remain; no provider timeout or eligibility check was reduced to produce the speedup.

Evidence: `.audit/scout-deeper-2026-10-16/summary.json`, `retrieval-benchmark.json`, `tests-final.log`, and `loading-*-mobile.png`. Browser tests exercise normal navigation through signup/confirmation/email with mocked matching and email, and inspect loading states by preventing navigation after the real loading handler. They cover invalid forms, duplicate sample clicks, reduced-motion CSS and history-reset behavior (a dispatched `pageshow` event).
