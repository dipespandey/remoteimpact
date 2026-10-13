# Backlink workspace

The dedicated staff workspace lives at `/admin/backlinks/`, alongside the employer Outreach page and accessible from **Backlinks** in both the admin navigation and Outreach header. Prospect cards support draft previews, copying, reviewed mail-app links, inline edits and workflow actions. It supports editorial resource inclusion, community feed partnerships, research citations and relevant directories. It never sends messages or purchases links. The model screens remain available at `/admin/jobs/linkprospect/` for detailed records and audit history.

## Set up

```sh
uv run python manage.py migrate
uv run python manage.py link_prospects --seed
```

Six public global-health career resource pages are included as starting leads. They are not endorsed placements or verified contacts. The admin also has an **Add six researched starting prospects** button; repeating it preserves existing records.

## Work a prospect

1. Add the exact resource page and research provenance. Choose sector, prospect type and priority.
2. **Inspect page** to observe its title, excerpt, public mailto links/contact-page candidates and any existing Remote Impact links. Candidates are not automatically adopted as recipients; check the contact and save its source URL.
3. Record the audience and a specific benefit for readers. Choose a durable Remote Impact category, guide, feed-sharing or research page as the target.
4. **Generate pitch**, edit it, then **Review and approve**. This requires a sourced contact and a complete draft. Editing its content, audience, target or contact invalidates the review.
5. Send the message manually outside this system, then **Record manual outreach**. This sets a follow-up date seven days later unless one is already recorded. Declined and do-not-contact prospects cannot generate new drafts.
6. Record replies and notes. Set **Placement reported** with a placement URL, then **Verify placement**. Review anchor text, `rel` attributes and page `noindex` signals. An observed target link is separate from a reported placement.

The metrics count all prospects, drafts, reviewed pitches, due follow-ups and target links observed at their last check. Filters and CSV exports support weekly review. Admin history and a read-only workflow log preserve edits, approvals, outreach records and check observations. Give staff the appropriate `jobs.*_linkprospect` permissions; page inspection and workflow actions require change permission.

## Discover more leads

The workspace includes focused search links. Bounded CLI discovery uses the project's existing DDGS library:

```sh
uv run python manage.py link_prospects --query 'climate careers "resources" "job boards"' --sector climate --limit 10 --dry-run
uv run python manage.py link_prospects --query 'climate careers "resources" "job boards"' --sector climate --limit 10
```

Search results enter the research queue without invented contact details or claims of relevance. DDGS availability varies; CSV import is an independent fallback. No paid-provider credentials are required.

Import CSV through the admin or CLI. Required header: `url`. Optional headers: `name,sector,kind,audience,fit_reason,target_url,priority`. Sector values: `climate,health,ai,impact`. Kind values: `resource,community,research,directory`. Priority: `1,2,3`. Maximum 200 rows and 128 KB. Imports are atomic, deduplicated by canonical page URL and never overwrite an existing prospect or opt-out. Reimports do not restore status or contacts from exports.

```sh
uv run python manage.py link_prospects --import-csv prospects.csv
uv run python manage.py link_prospects --inspect --id 1
uv run python manage.py link_prospects --verify --limit 10
```

## Verification limits and measurement

Inspection uses the existing bounded, public-network-only fetcher with validated redirects. It executes no page JavaScript. Up to 20 contact candidates and 50 link observations are retained per check, prioritising exact target matches; the check log records coverage counts. Verification matches the target hostname, path and functional query parameters, ignoring fragments and standard campaign parameters. Links on Remote Impact itself and redirects back to Remote Impact do not count as external placements. Verification does not check indexing, guarantee ranking credit or infer link quality. Failed requests remain unknown; check history retains prior evidence. Inspecting the prospect page again replaces the last check, so verify the placement again to refresh placement evidence.

Prioritise audience relevance and referral value over provider authority scores. Generated pitches use a campaign URL with a unique `utm_source=link_prospect_ID`. Review those campaigns in your analytics and enter referral visits and Scout signups under **Measured results**, with the reporting window and source report. Blank counts mean unmeasured, while zero is a recorded zero. The workspace totals these records and saves measurement changes in its event log; it does not automatically attribute conversions or import a competitor backlink index. Use distinct prospect campaigns and consistent windows to avoid double counting. Assess results after a month of small, specific pitches before expanding automation.
