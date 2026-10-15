# Scout runtime integration

`jobs.services.scout_remote_policy.check_matches` runs after relevance review and
close-match filling, before final caveats, tiers and result limits. Preview and
saved Scout deliveries share this path. Enable with
`SCOUT_REMOTE_POLICY_ENABLED=true`; the default is false and setting it false
restores the previous selection behavior after a web-worker restart.

The first deployment supports one explicitly requested country. Regions,
worldwide searches, multiple alternative locations and ambiguous Georgia
requests retain the existing Scout checks. A work-from request supplies only the
country; it never establishes authorization, state, contract type or working
hours. Additional applicant facts are deliberately not inferred from profiles.

Every selected opening receives a rules decision. Incompatible openings are
removed; unknown openings retain an employer-confirmation caveat and cannot be
classified as good matches. Compatible policy decisions cannot erase other
caveats or revive previously rejected openings. This can reduce the result count;
source-backed recovery may fill removed slots from the already retrieved pool.
Recovered openings pass the same policy gate without another API call or query.

One optional DeepSeek batch interprets at most six unresolved policies. It uses
the application's configured provider/model and existing four-call, 18-second
Scout budget, with a three-second request timeout and no retries. The request
contains source posting segments and the requested country, without the user's
name, email, CV or free-form brief. Sources over the model input limit are not
truncated to obtain an answer. Missing applicant details block fallback.

Responses require exact candidate IDs, valid labels and source-segment citations.
Malformed responses, timeouts, cache failures and exhausted budgets preserve
unknown. Model decisions are cached for 24 hours using the complete source,
country, provider model and prompt/version hash. Source or model changes miss the
cache. A source citation establishes attribution, not semantic correctness.

Diagnostics include `remote_policy`, `remote_policy_counts`,
`remote_policy_cache_hits` and `remote_policy_api`. Each returned job also carries
`scout_remote_policy` with its label, backend and source evidence; unknown caveats
are included in normal Scout reasons and persisted by the existing delivery flow.

No trained classifier, local Ollama service, model weights, training corpus or
database migration is required. Research reports and their frozen implementation
hashes describe the experiments in `MODEL_CARD.md`; they do not independently
validate this deployment adapter or establish customer accuracy.

## Availability recovery

Scout workflow version 4 caps interpretation at 2.5 seconds and shares a
three-second preparation allowance across interpretation and planning. A failed
interpretation skips planning. Retrieval leaves six seconds for evidence review;
close-match review reuses the existing candidate pool where available and skips
another provider request after a failed primary review.

When fewer than three results survive, `scout_recovery` can select possibilities
with an explicit profession in the request, a matching role title, and
applicant-owned duties in the source. Supported function signatures are explicit
and intentionally limited. Background/skills narratives alone, qualifications,
company missions and generic requests are insufficient. Original eligibility,
salary, work arrangement, exclusion and seniority gates still apply. Validated
negative relevance decisions and policy conflicts are not restored. Reposts are
deduplicated. All recovered results carry `source_review_pending` and remain in
Worth considering. `SCOUT_RULE_RECOVERY_ENABLED=false` disables this path.

`source_rule_recovery` records the number returned by this path; `recovery_policy`
records its policy checks. `review_incomplete` remains true for results awaiting
full review, even if all three slots are filled. Empty searches record
`empty_reason` separately from the result count. Replay reports measure behavior
on selected cases, not population-wide relevance or a guaranteed fill rate.

For a bind-mounted VPS deployment, ship the committed runtime Python files and
`countries.json`, and mount `ml/remote_policy` at `/app/ml/remote_policy:ro` in
addition to the existing jobs/jobboard mounts. Back up touched source files,
Compose configuration and environment before enabling the flag. Recreate the web
container when adding a mount or changing its environment. A normal Docker image
build already includes the runtime package via `COPY . .`.

Verification:

```bash
python manage.py test jobs.tests.test_scout_remote_policy --noinput
python -m unittest ml.remote_policy.test_policy
python manage.py check
```

After deployment, check the flag inside the running container, run source-backed
rules and bounded API smoke checks, verify the shared matching path, and check the
container health and public Scout preview. Retain the backed-up configuration and
sources for rollback; never reset the VPS's unrelated working tree.
