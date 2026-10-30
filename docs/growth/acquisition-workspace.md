# Acquisition workspace

`/admin/growth/` connects existing partner and employer workflows to a bounded daily
research and measurement loop. Staff can see the next three opportunities, review a
prepared contribution, inspect its evidence, follow the original outreach record,
and compare recorded campaign outcomes. This is an additive first release, not a
new CRM or a claim of autonomous customer acquisition.

## Automatic work

A versioned cron entry checks the active immutable release every 15 minutes. A
single database lease permits one cycle per 24 hours or an explicit staff refresh.
Pause is checked between stages. A cycle rotates one climate, health, AI or impact
resource query (up to five discoveries), inspects up to ten existing partner pages,
refreshes up to ten employer opportunities and measures up to fifty campaigns.
There is a three-minute stage-start budget and a ten-minute crash lease. Individual
HTTP inspections use the existing SSRF-protected fetcher and its eight-second limit.
A dependency call already in progress can finish after a pause. Stale leases permit
recovery; incomplete runs retain their running state for diagnosis.

New results are research, never permission. Page text cannot approve a contact,
change channel policy, run commands or instruct a language model. Preparation uses
rules and templates, with no paid provider or model calls. Scores are explainable
priorities, not conversion probabilities. Source fit/contact details need review.
A small bonus uses previously observed Scout outcomes, not invented revenue.

Drafts include a diverse sample of current catalogue jobs and source links; they
are not independently verified employer adverts. Salary and worldwide eligibility
are not invented. Staff must check the current employer source before publication.
Human-edited drafts are preserved, including when research finishes concurrently.
Dismissed/held campaigns and source opt-outs are not automatically resumed. Reddit
is excluded from discovery and preparation while its existing hold remains unresolved.

## Distribution boundary

This release automatically researches, prepares and measures. It does not send
emails or post publicly. The detail view links to the existing permission-checked
Backlinks/employer workflow, where contacts, approvals, replies, opt-outs and
placements are already recorded. Source status and placement evidence flow back on
refresh. Existing Reddit/X/LinkedIn routines are not changed. Do not clear a hold or
infer approval from page prose. Sender automation must use a separately reviewed,
idempotent connector and explicit per-channel policy before it can be enabled.

## Attribution and money

Partner campaigns reuse `link_prospect_ID`; employer proposals use
`growth_employer_ID`. Both preserve coherent campaign labels through existing
signup/checkout attribution. Successful public landings count at most once per
session/campaign/day, exclude known bots and staff, store no IP or browser identity,
and retain at most twenty dedupe labels in the session. Counts can still include
scanners and are not unique people. Recording failures never fail browsing.

Thirty-day observations combine directly tagged events with later actions by the
same campaign's account-signup cohort. Anonymous activity without tags is unknown;
this does not identify a Reddit person as an account. Returns are observed events
on days 1–7 for mature signup cohorts, capped at 1,000 records. This is not a complete
pageview retention metric. Graphs are event totals, not a guaranteed ordered funnel.
Campaign measurement timestamps/windows are visible; first-party landing counts
only begin after activation. Cash is matched by exact employer job-posting payment
intent to existing live, scope-verified reconciled processor records. Trials and
payment-completion events are not money. Feature/subscription cash, untagged payments,
and absent/stale reconciliation remain outside attribution; currencies are separate.

## Operations and release

Run `python manage.py run_growth_cycle --due` for scheduled work, or omit `--due`
for an immediate bounded cycle (pause and lease still apply). The admin refresh
button requests work rather than doing network calls inside a web request. Run
history distinguishes complete, partial and failed work and stores safe error
summaries. Unknown provider errors do not record raw responses or credentials.

Migration 0004 creates four new tables only, with no rewrites of existing data.
Older releases ignore them. The release installs only its own cron file, preserves
unrelated schedules and refuses to overwrite an unmanaged file. Its dispatcher checks
command availability so rollback to a release without this feature is a safe no-op.
Deploy through the tested main workflow with reviewed migrations enabled.

Permissions require campaign, prospect, consumer/employer event and cash read access;
editing campaigns and controlling automation have separate permissions. All mutations
are CSRF-protected POSTs. No tracking event, internal customer identity or private
analytics payload is passed to the discovery provider.
