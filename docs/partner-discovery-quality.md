# Partner discovery quality

Growth rotates one sector per daily run. Within the selected sector it rotates
through up to three intents (university resources, communities/newsletters,
resource suggestions and partnerships). A sector's next daily visit uses a
different starting intent. Manual refreshes on the same day are repeatable.

Search retrieval and qualification are separate. Each query returns at most
five hits; the worker checks at most eight new pages and imports at most five
candidates, within a 95-second discovery budget and the overall run lease.
DDGS uses the application lockfile; do not update only its HTTP dependencies.
No language model, paid search API or autonomous outreach is used.

## Candidate handling

- Reject search/ad redirect destinations, social platforms excluded from this
  workflow, private/invalid URL literals and our own site before fetching.
- Remove campaign parameters. Collapse www and parent/subdomain variants when
  the parent site already exists in the queue, including opted-out records.
  Unrelated sibling university and hosted-site subdomains remain separate.
  This is website deduplication, not a corporate ownership database.
- Use the shared SSRF-safe, size-limited public HTML fetcher; validate redirects
  again before import. Never bypass blocked pages.
- Assess main page content for sector audience, career resources, editorial
  route hints, competing-board signals, contact restrictions and explicit
  publication/modification dates. Dates over two years old need review;
  undated pages stay explicitly unknown. Footer copyright years are ignored.
- Import promising or relevant-but-incomplete pages into research. Failed
  fetches and poor fits are retained in the bounded run evidence, not imported.
  A promising score does not establish willingness to accept a placement.
- Store sourced assessment separately from staff-authored fit/contact fields.
  Editing URL or sector invalidates the assessment. No discovered contact is
  automatically accepted. Existing drafts, opt-outs and contacted status remain.
- Growth's Ready for review requires a recent promising page assessment plus
  recorded audience fit and a sourced contact. Sending remains in the existing
  reviewed workflow; Reddit's separate publishing hold remains unchanged.

The Growth page shows search hits, checked pages, promising pages, review
candidates and imported records separately, with filtering/failure coverage.
Backlinks shows the same page assessment and can filter by research quality.
Counts describe bounded research, not unique visitors, conversions or revenue.

## Evaluation

Run a frozen labelled evaluation without network access:

```
python manage.py evaluate_partner_discovery --dataset jobs/tests/fixtures/partner_research_cases.json
```

The bundled labels are **synthetic regression examples**, not evidence of
production lead precision. For a representative audit, save independently
reviewed page snapshots as a JSON object with `description`, `as_of` (ISO date),
and `cases`: `id`, `url`, `sector`, `html`, optional `fetch_status`, and
`expected_status` (`promising`, `needs_review`, `unsuitable`, `unverified`).
The command reports decision matches, promising precision/recall and errors;
it reports null where there is no denominator. Keep uncertain human labels out
of scored cases. Do not tune and report on the same held-out dataset.

Run a live bounded sample without importing anything:

```
python manage.py evaluate_partner_discovery --live climate --output /tmp/climate-research.json
```

Live output is unlabelled; rule scores are not measured quality. Evaluate the
source page, actual audience, current resource purpose and editorial/contact
route, then record independent labels. Track actual referral signups and
revenue separately in Growth; searches alone cannot validate acquisition.

## Migration and rollback

`jobs.0087` adds one nullable JSON column. Existing rows need no backfill and
old application versions can keep inserting rows without the column. Apply
through the reviewed GitHub workflow with migrations enabled; do not reverse
this additive migration during an application rollback.

## Evaluation during implementation (30 October 2026)

Seventeen labelled synthetic cases passed, including irrelevant navigation text,
old articles, unavailable pages, competing boards and a general newsletter that
does not establish career distribution. These validate specified behaviour only.

Read-only live climate and health samples each fetched eight pages. The final
health run returned ten hits: one duplicate, two fetch failures, one unchecked
at the page cap, one needs-review page and no promising candidates. It imported
nothing. The general-newsletter false positive seen in an earlier sample prompted
a stricter rule and regression case. Results varied between searches; this is why
coverage and per-page evidence must remain visible. No live precision, contact
acceptance, customer acquisition or revenue improvement has been established.
