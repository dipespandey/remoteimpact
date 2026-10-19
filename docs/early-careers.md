# Early careers directory

Public hub: `/early-careers/`. Programme profiles are separate from job rows: they do not create duplicate vacancies, produce JobPosting schema or inflate job inventory.

The reviewed directory lives in `jobs/data/early_careers.json`. Each entry records the official source, source-review date, application status, eligibility, funding/cost, format, location and duration. Unknown details remain explicitly unconfirmed. Training with fees is not classified as paid work. Remote-with-travel and placement-dependent arrangements are not included in the fully remote filter.

## Updating programmes

Review the official source before changing `checked_on`, status, eligibility, funding, location or timing. Use `open` only when the current source explicitly supports an open application round. A general vacancies/projects page uses `listings`, not an assertion that a particular vacancy is open. Use `check` when the next intake is unconfirmed and `closed` for an ended round. Do not infer a deadline year or timezone from an ambiguous date.

Set an ISO `deadline` only for a confirmed date. After that date, the page automatically labels the round closed. Open/listings claims expire to “Check next intake” once the source review is over 14 days old. This is a stale-claim safeguard, not an automatic source monitor: a human/agent still needs to review and deploy the next update. No scheduled automation was added.

The registry is cached in each application process. Publish edits through the normal immutable blue-green deployment; editing the host file alone does not update the running app.

## Filtering and search

Filters use GET and work without JavaScript. They intersect rather than silently broadening eligibility. Empty combinations offer a clear reset. Filtered pages are noindex and canonicalize to the hub. The hub is in the static sitemap and links from seeker desktop/mobile navigation and the shared footer. The JSON-LD is CollectionPage/ItemList only. Source-link and filter actions use existing `data-track` analytics.

Run `python manage.py test jobs.tests.test_early_careers` after data or logic changes. Check 390px and 1024px layouts, combined filters, zero-result recovery, and the live page/sitemap after release. Dates in programme evidence are not claims of a guaranteed admission, remote placement or job.

## Expansion reviewed 19 October 2026

53 distinct programme profiles, expanded from 15. Discovery included 80,000 Hours and Probably Good; profiles link to organisers' sources. Multiple UNDP programmes have different entry routes and are counted separately; individual tracks of LawAI, Animal Outlook and New Roots are combined. External discovery boards are supplementary links, excluded from counts and schema. These are programme profiles, not a claim of 53 open vacancies.

Coverage now includes humanitarian/refugee support, food security, global development and health, animal welfare, climate/conservation, education/culture, AI safety/governance and social entrepreneurship. Search matches organisation, name, sector, location, fit and requirements; it intersects with existing GET filters. Unpaid placements have their own funding label. Cost-covered courses are distinct from employment.

Some organisers' older pages contradict newer policies (notably UNFPA stipend policy). The registry uses the linked policy where appropriate. Funding, recruitment windows and location remain explicitly unconfirmed when the reviewed source does not establish them. Programme-specific review dates are shown inside details; the hub does not imply every source was reviewed on the newest date.
