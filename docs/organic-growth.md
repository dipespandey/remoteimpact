# Remote Impact organic growth

The target is 10x **qualified organic traffic**, with Scout signups and application
clicks as conversion checks. Set the numeric target from verified Search Console
and GA4 periods. Both sources are now connected read-only to the local SEO CLI;
query only `sc-domain:remoteimpact.org` and GA4 property `518242262`, with Remote
Impact hostnames. Keep private baseline exports in the local audit directory. Raw job
views, URL notifications and catalogue counts are not substitutes for that baseline.
Do not infer search volumes or competitor traffic from hypotheses.

## What runs automatically

- Public role and keyword pages use the job board's availability policy, including
  fresh source sightings on older postings. Their current counts, employer coverage,
  recent postings and links to relevant impact hubs/guides update from inventory.
- Import duplicate detection and daily cleanup recognize employer-page campaign
  copies and BambooHR source tags, preserving functional URL IDs and case-sensitive
  paths. Proved duplicates are retired and their bookmarked URLs redirect to an
  available retained posting. Different source URLs without shared identity stay
  separate until verified; matching titles alone are insufficient.
  Feed refreshes preserve a confirmed retirement while its canonical posting is
  available. A changed posting identity or unavailable keeper may reopen normally.
- Sign-in, signup and sign-out templates carry `noindex, follow`; public job
  pages retain their own availability and indexability policy.
- New-job IndexNow notifications start after transaction commit. The existing
  two-hour batch handles subsequent changes. Accepted content fingerprints live
  in the database, so unchanged sightings, embedding updates and page-cache resets
  do not cause repeated submissions. Closures and reopenings each create a change.
  Only previously acknowledged public URLs are sent as removals; drafts are skipped.
- Google job notifications use the same public policy and persistent acknowledgements.
  Missing credentials and failed requests stop with an explicit error. The existing
  daily schedule reserves 190 update notifications and 10 removals. Google enforces
  its approved project quota; accepted notifications do not prove indexing.

Google's API is free and restricted to eligible job/video pages, not general
category pages or articles. It needs a Google Cloud service account, Indexing API
enablement, verified Search Console ownership, and the applicable approval/quota.
Search Console's read-only SEO connection is separate from that publishing credential.
Keep the publishing key in the VPS secret configuration, never in Git or chat.

As of October 13, 2026, the dedicated publishing project is
`remote-impact-indexing`. Its `job-indexing-publisher` service account has no
Cloud project roles and is a delegated owner only of `sc-domain:remoteimpact.org`.
The Indexing API is enabled. Production reads its JSON credential from a protected,
read-only secret mount; the key is outside the repository. All 190 initial eligible
job notifications were accepted, with zero failures. IndexNow also accepted an
initial batch of 3,575 pending job URLs with HTTP 200. These acknowledgements prove
receipt, not indexing or ranking.

Search Console's server-error detail report currently retains 768 historical
affected URLs (last update October 9), with validation started October 11. Keep
that validation running and compare its examples against live responses before
claiming the historical issue is resolved. The overview recommendation can show a
different count; use the dated detail report for this audit.

Sources: [Google Indexing API](https://developers.google.com/search/apis/indexing-api/v3/quickstart),
[quota and pricing](https://developers.google.com/search/apis/indexing-api/v3/quota-pricing),
[IndexNow](https://www.bing.com/indexnow/IndexNowView/IndexNowGetStartedView).

## Daily work, weekly decisions

Read `organic_growth_report` for first-party inventory and last notification
acknowledgements. Use `seo` report evidence for technical checks. With the connection,
use Search Console for non-branded queries, existing pages near useful positions,
click-through opportunities and real demand. Compare equal periods and note
job-inventory changes, indexing lag and seasonality. Track dated changes against
organic Scout signups and outbound application clicks in GA4.
The current generic `form_submit` key event is not a verified signup. Track
`job_scout_created` and `apply_click` separately, and retain their event definitions.
The daily growth heartbeat runs in the existing Codex chat; it selects at most
one evidence-backed improvement per run and reports meaningful changes.

```sh
.venv/bin/python manage.py organic_growth_report
.venv/bin/python manage.py indexnow_submit --hours 24 --limit 200
.venv/bin/python manage.py ping_google_indexing --hours 26 --dry-run
```

Prioritize one evidence-backed improvement at a time: relevant internal links,
useful role/sector page content, factual metadata, structured data and fast public
HTML. Retain a bounded crawl before and after, and account for every returned
finding and URL. A heuristic requires review; it is not automatically a defect.

Authority comes from useful assets and relevant audiences: the existing hiring
snapshot, salary coverage, career guides, tools, employer directory, feeds and
partner page. The Backlinks workspace can organize researched career-community
prospects and drafts. This workflow does not authorize sending outreach, posting
to external communities, buying placements or purchasing provider subscriptions.

The initial 100-page crawl is capped and cannot establish site-wide coverage.
Three duplicate-title pairs share actual employer posting destinations; the two
CIVICUS source copies have different destinations and remain a research task.
Wide-title observations retain important role, sector and country wording early;
do not remove relevant details merely to satisfy a pixel estimate.

FirstIndex's public website describes a $2,000/month managed workflow covering
research, content, tools, fixes, index checks and AI visibility. We can run an
evidence-based workflow with the existing stack, but its example growth figures
do not forecast Remote Impact's results. A cadence does not guarantee 10x traffic.


### Organization metadata and public guide follow-up — 2026-10-13

Deployed `219f3f6` and `a6fd583`. Organization profiles derive categories and descriptions from available inventory; removed fixed internship/eligibility claims and request-time modification dates. Directory eligibility was aligned in `1edc6b4`.

Public application-guide GET/HEAD requests now read a valid saved guide or return immediate, clearly labeled general guidance. They never call a model or save generated content. Generic fallback pages are noindex; valid saved guides retain existing indexability. The generation service remains available for explicit offline work; page views no longer generate guides.

Verification: 45 relevant tests passed. Three production profiles and two guide pages returned HTTP 200. Sample guide responses were 209ms cached and 254ms fallback, not a sitewide performance guarantee. Empty employer profiles and generic guides intentionally remain noindex. Complete scoped findings and URL dispositions: `.audit/seo-profile-guide-handoff.html` and JSON. Broader SEO review remains active.


### Reviewed posting consolidation — 2026-10-13

Retired eight aggregator copies only after exact Ashby, Workable or Personio posting-ID matches and available employer-board keepers were revalidated under transaction locks. Added permanent redirects; all eight old URLs were crawled and returned 301 to the intended HTTP 200 destination. Full row backup and plan are privately retained on the VPS under `/opt/remoteimpact-backups/2026-10-13-release/reviewed-postings/`. Recruitee slug-based pair was excluded from this strict-ID batch.

A 500-page technical crawl returned three grouped findings (42 duplicate-title URLs, two duplicate-description URLs, 35 title-width review URLs). All 79 affected rows were retrieved; crawl coverage is capped and provider joins were skipped. This crawl overlapped the cleanup, so a second identical report was started for post-cleanup reconciliation. Handoff: `.audit/seo-consolidation-handoff.html` and JSON; do not treat pending findings as resolved.


### Homepage cold-cache bottleneck — 2026-10-13

Profiling isolated a 13.2-second public trust-statistics rebuild; its historical removal aggregate alone took 12.5–14.1 seconds. Filtering recent events preserves exact counts but still takes several seconds. `39cccbf` moves rebuilding to `refresh_public_statistics`, saves a durable `PublicStatisticSnapshot`, and makes public reads use that snapshot. VPS cron refreshes at minutes 7/22/37/52 with flock. Snapshot timestamp stays visible on /how-we-check/. Cache clears do not trigger public aggregation; missing snapshots hide unavailable figures.

Production cache-miss proof: 64ms, one snapshot query, no job scan. Fresh public crawl: homepage 228ms, /how-we-check/ 161ms, both HTTP 200 and no reported issues. Full suite: 695 tests passed with 5 skipped. Migrations 0077/0078 applied; concurrent expression indexes validated, but planner retained a scan in the filtered benchmark, so the visitor-latency fix is durable snapshots rather than a claimed index-only speedup.

`30b05cc` also removes repeated exact employer attribution from search titles. A ninth reviewed duplicate (Recruitee) was consolidated after checking the employer board, with verified 301 -> 200. Latest completed 500-page sample has 28 duplicate-title URLs, 33 title-width observations and no duplicate-description finding. Full scoped handoff: `.audit/seo-title-cleanup-handoff.html` and JSON. Final same-input rerun after snapshot deployment was started; broader review remains active.
