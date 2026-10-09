# SEO Tooling

Last verified: 2026-09-11

## Installed tools

- **SEO Skill CLI**: `seo` v0.2.40 is installed locally. It performs no-login technical crawls and can use optional read-only Search Console and Analytics connections. The current Remote Impact crawl was run with:

  ```bash
  seo report --url https://remoteimpact.org --actions-only --json
  ```

- **OpenSEO agent skills**: installed from `every-app/open-seo` into `.agents/skills/`. `skills-lock.json` records the source and content hashes. The bundle includes project setup, SEO coaching, audits, keyword research and clustering, competitive analysis, link prospecting, and local SEO workflows.

## Evidence boundary

The local SEO Skill crawl does not require an account. A fresh 100-page crawl completed with no high issues, one medium external-link observation, and 124 low issues. The crawl was intentionally bounded, so its sitemap inventory and orphan analysis are partial.

Its actionable observations are:

- 87 job pages have titles that may truncate on some devices.
- 37 job pages had no observed internal link in the crawl sample. Sitemap inclusion still provides discovery, so this needs page-architecture review rather than a blanket fix.
- One job page had multiple H1 headings because imported job-description HTML contained its own headings. The shared `render_html` filter now demotes imported H1 tags to H2 tags, preserving one page-level H1.
- One current Nextus job points to an Idealist URL that returned 404 to the crawler. A public search result still resolves to that same listing, so this is being treated as a source-side bot/access inconsistency until the source URL is confirmed unavailable rather than deactivating a newly published job.

OpenSEO's hosted MCP is not connected in this environment. Its keyword, backlink, and competitor reports require either an OpenSEO connection or a research provider such as DataForSEO. We should not present manual keyword hypotheses as measured demand. The committed `.seo/` files remain the source of truth for current SEO positioning, page inventory, and research assumptions.

## Operating rule

Run the local crawl before SEO changes, verify each finding against live HTML, make the smallest supported change, then rerun the same report. Use OpenSEO workflows when its MCP is connected for provider-backed keyword, competitor, and backlink evidence.
