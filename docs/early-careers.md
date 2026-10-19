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

## Private shortlists and conversion evidence

The `earlycareers` app stores account-owned `ProgrammeBookmark` rows and coarse `DirectoryEvent` evidence. The additive initial migration creates two new tables and does not alter existing jobs, user records or subscriptions; it is compatible with the retained release. Programme slugs remain independent of Job rows. Account deletion cascades to bookmarks and anonymises event ownership.

Anonymous Save uses CSRF-protected POST, stages only known programme slugs in the session, and hands off to allauth with an internal return path. Pending selections expire after 24 hours and are merged idempotently at login, including an existing-account login. Generic account creation stages no empty bookmark. A contextual signup page explains the benefit; Google/LinkedIn keep the same return path. Programme sources can be opened without an account. Save does not subscribe anyone to marketing or deadline emails.

Saved-only pages and all personalised responses are no-store. Users can remove programmes or mark them applied; records are scoped to the authenticated account. Official-source redirects use only registry URLs, never a user-supplied destination. Closed rounds are collapsed on the unfiltered hub and expanded when filtering. Sector aliases are consolidated, visible eligibility summaries do not claim guaranteed eligibility, and paid first-step presets still retain country and degree restrictions.

`python manage.py early_careers_report --days 30` reports visits, account handoffs, directory-assisted account creation, saves, applied markers and source openings. Known bot user agents are excluded, and visits/handoffs/source opens are deduplicated per day using a coarse visitor key. These are observational counts, not verified unique humans or proof that the page caused a signup. Campaign labels are retained without storing IPs, free-text searches or external referrer URLs. Existing signup analytics also receive `entry_point=early_careers`.

Run `python manage.py test jobs.tests.test_early_careers earlycareers jobs.tests.test_attribution` for account handoff, signup completion, privacy, CSRF, redirect safety, expiry and curation checks. The full signup test mocks the email provider. Browser checks should stop at the live signup form rather than creating production test accounts or sending welcome mail.
