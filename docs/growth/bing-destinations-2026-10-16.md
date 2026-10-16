# Bing destination improvements — 16 October 2026

## Scope and evidence

Browser-observed Bing results and Webmaster data identified education, sustainability and nonprofit as existing useful destinations. The three-month keyword screenshot and six live searches are separate evidence: live positions vary by session/device/market. The 30-day page report showed nonprofit 128 clicks, sustainability 102, education 18; this is not a conversion report. The All-filter overview is not interchangeable with the Web-only keyword table.

The registered `audit-page` report returned zero recognised fixes/reviews for each page. Acquisition completeness was undeclared; this does not establish a sitewide all-clear. Full page evidence additionally showed machine-learning engineering titles in the education results. These semantic membership errors were not detected by the report's technical checks.

## Changes and measurement

- Education now requires an education responsibility in the title, rather than matching any occurrence of "learning" or "student". Added tutor, instructional design and training coverage. It intentionally excludes pure machine-learning engineering, explicit AI-tutor/model-training jobs, generic working-student roles, MEL consultancies and fellowships for students without an education responsibility.
- Education has a stable title and clearer description, without a volatile vacancy count or a claim that remote means anywhere. Sustainability and nonprofit retain their established destinations; no further URL consolidation is introduced here.
- Education and sustainability offer title/location-label filters, nonempty specialty shortcuts, preserved pagination, canonical root URLs and noindex for filtered variants. These location labels do not establish applicant eligibility. Empty states retain filters and offer a reset/wider search.
- Three destinations offer a relevant, editable Scout preview; visiting the listing page does not create an account, subscription or email. The preview source is one of `seo_education`, `seo_sustainability`, `seo_nonprofit`.
- Targeted pay summaries identify separate minimum/maximum sample sizes. Catalogue counts say listed roles and ask visitors to confirm availability at the employer.
- GA4 events: `seo_landing_view`, `seo_job_view`, `seo_apply_click`, `seo_scout_click`, `seo_scout_visit`, `seo_signup_visit`. A click is not an application submitted; a visit is not a successful preview/signup. Event parameters `landing_group` and `discovery_source` are fixed enums. Register these as event-scoped custom dimensions in GA4 to use them in Explorations. No briefs, search text, CV contents, email addresses, job IDs or full referrer URLs are sent by this new instrumentation. DNT and non-production hosts suppress the new events. Thirty-minute tab-local attribution can be lost through storage blocking or cross-device navigation.
- `manage.py search_destination_report --days 30` reuses existing server-observed Scout flows to report preview → signup → Scout stages. Subsequent billing completion/trial events are reported only for new accounts linked to those flows. They are not cash receipts or causal revenue attribution. Legacy and future missing evidence stays unlinked; no historical backfill.

## Outreach handoff — no new messages sent

Use existing contacted prospects and their recorded follow-up eligibility; do not duplicate outreach or infer placement from a queued email. Match each prospect's audience to the existing nonprofit, education or sustainability destination. Link education resources to `/remote-education-jobs/`, general nonprofit careers to `/impact/remote-nonprofit-jobs/`, and sustainability resources to `/remote-sustainability-jobs/`. Prefer specific relevant editorial resources. Track accepted live placements separately from messages sent. No new outreach or automated follow-up is authorized by this release itself.

## Evaluation

Compare equal 28-day Bing Web periods after recrawling, separating page/query metrics. Keep screenshots/export dates and affected URLs. In GA4, compare landing group + discovery source with job/apply/Scout events. Use the server cohort report for observed signup/Scout stages. Check sample size and observation time before interpreting rates. Do not promise a rank/CTR increase. Paid revenue still requires genuine, verified payment receipts; existing trial/completion events must not be presented as revenue.

Review dispositions: education relevance and metadata changed; destination filters and Scout handoff changed; salary/count explanations changed; existing winning URLs preserved; additional similar pages not needed for this experiment; new outreach deferred pending a specific send instruction and existing-contact checks; rankings and customer outcomes await new observations.

GA4 setup: registered event-scoped custom dimensions Search destination (`landing_group`) and Discovery source (`discovery_source`) in the connected Remote Impact property, verified after reload. New reporting data requires processing; no retrospective values are invented.
