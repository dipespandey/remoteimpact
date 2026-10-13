# Remote Impact organic growth

The target is 10x **qualified organic traffic**, with Scout signups and application
clicks as conversion checks. Set the numeric target from verified Search Console
and GA4 periods; neither source is connected to the local SEO CLI yet. Raw job
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

Sources: [Google Indexing API](https://developers.google.com/search/apis/indexing-api/v3/quickstart),
[quota and pricing](https://developers.google.com/search/apis/indexing-api/v3/quota-pricing),
[IndexNow](https://www.bing.com/indexnow/IndexNowView/IndexNowGetStartedView).

## Daily work, weekly decisions

Read `organic_growth_report` for first-party inventory and last notification
acknowledgements. Use `seo` report evidence for technical checks. Once connected,
use Search Console for non-branded queries, existing pages near useful positions,
click-through opportunities and real demand. Compare equal periods and note
job-inventory changes, indexing lag and seasonality. Track dated changes against
organic Scout signups and outbound application clicks in GA4.

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
