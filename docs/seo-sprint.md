# Remote Impact SEO Sprint

Initialized: 2026-09-11
Stack: Django 5.2 with controller-based URLs and server-rendered templates
Primary conversion: free one-time AI Job Scout result, then $10/month Pro for ongoing monitoring

## Baseline

Technical foundations are already present: canonical tags, meta descriptions, Open Graph fields, Schema.org JSON-LD, robots.txt, a sitemap index with sharded job sitemaps, RSS, and public category/role/keyword landing pages. The live site returns successful responses for the main SEO surfaces.

Current product-data caveats that affect SEO trust:

- The active inventory is concentrated in Climatebase and Idealist.
- Many active jobs are older than 30 days, so freshness must remain visible and stale roles must not dominate lists.
- Salary coverage is materially incomplete and should never be implied to be universal.
- Exact application-URL duplicates and repeated organization/title combinations need continued cleanup.

## Phase tracker

### Phase 0 — Foundation and technical hygiene

- [x] Detect stack and routing convention.
- [x] Document positioning, audience, voice, anti-positioning, and commercial paths in `.seo/brand.md`.
- [x] Inventory internal link targets in `.seo/link-inventory.md`.
- [x] Establish a manual keyword hypothesis ledger in `.seo/keyword-research.json`.
- [x] Add programmatic-page social metadata so role and keyword pages share their actual title and description.
- [x] Keep filtered job/category variants `noindex, follow` to prevent crawlable faceted duplication.
- [x] Keep only current/high-quality job pages in the sitemap through `visible_jobs()`.

### Phase 1 — Improve existing high-intent pages

- [ ] Add a visible “updated” or “last checked” trust signal to role and category landing pages.
- [ ] Add a compact source-methodology section explaining aggregation, application destinations, and freshness.
- [ ] Add targeted internal links between role pages, impact-area pages, guides, and the Job Scout.
- [ ] Make stale-role handling consistent between job lists, landing pages, and sitemaps.

### Phase 2 — Publish evidence-led content

- [ ] Publish a dated remote impact jobs market snapshot using repeatable inventory queries.
- [ ] Publish a country/region guide only after country completeness and counts are reliable.
- [ ] Publish one practical climate-career playbook with live job and guide links.

### Phase 3 — Distribution and authority

- [ ] Offer the partner page, RSS feed, and embeddable attribution guidance to career centers and impact communities.
- [ ] Use the data snapshot as the outreach asset for legitimate editorial links.
- [ ] Maintain a list of referring domains and record requested, live, and rejected placements.

## Quality gates

Before shipping an SEO page:

1. It has one clear search intent and a unique title, description, canonical URL, and H1.
2. It contains meaningful current data or advice beyond a templated list.
3. It links to at least one live job search, one supporting guide or role page, and the appropriate Scout/plan path.
4. It does not make universal claims about salary, geography, verification, or job freshness.
5. It is included in the correct sitemap only when it has indexable value.
6. Its structured data parses as valid JSON and reflects the visible page.

## Next highest-value phase

Improve the existing role and impact-area pages before generating more URL volume. The most credible near-term SEO win is stronger trust and internal linking around pages that already have live inventory, followed by a dated data study that can earn links from communities and career resources.
