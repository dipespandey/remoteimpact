# Revenue target and feature freeze

Decided 2026-11-09. Review every Monday until the target is met or the decision is revisited.

## Where we started

Lifetime revenue on 2026-10-28 was one $99 job post and one $10 Pro renewal. About 2,980 registered accounts produced 70 to 74 signed-in active users a week. The weekly roundup had 8 confirmed subscribers, while 564 legacy audience members had never been asked to confirm. Google clicks were about 98% brand searches.

The product and the payment flows work. Distribution and selling do not, so this phase spends effort on those.

## Target

- **Revenue:** $1,000 collected in January 2027, from employers first. Employer products are $49 feature, $99 post, and org plans from $149/month.
- **Leading indicators:**
  - 500 confirmed weekly roundup subscribers by 2026-12-15.
  - 25 employer offers sent per week.
  - 10 paying employers by 2027-01-31.

## Feature freeze

Until the leading indicators move, do not start:

- new Scout relevance, ranking, evidence or model work
- new SEO page types, keyword pages or crawler-policy changes
- new measurement layers on top of the existing funnel events

Still allowed:

- Fixes for production bugs, security and data quality that users can see.
- Work that directly serves the target: the re-consent campaign, employer offers, checkout and claim friction, and the roundup itself.
- Making existing sector pages and AI-assistant pages easier to find and act on, without adding new page types.

An agent asked for frozen work should say it is frozen and point here. The owner can lift the freeze in chat.

## Campaign commands

Run these on production with `/opt/easyclaw/exec-active python manage.py ...`. Each is a dry run unless you pass `--send`, and each records every send, so no person or organization gets the same campaign twice.

- `send_roundup_reconsent --limit 100`: sends each legacy audience member one request to confirm the weekly roundup. Nobody is subscribed without clicking. Run one batch a day until the dry run reports 0 eligible.
- `send_employer_offer --limit 25`: emails unclaimed organizations whose live roles got apply clicks from us in the last 30 days. Each email shows their views and clicks and offers a $49 feature for the most-clicked role. It skips anyone reached by an earlier employer campaign. Run weekly. Record each "no thanks" reply by hand so nobody is emailed again.

## What runs without the owner

All of these run through `/opt/easyclaw/exec-active` from the root crontab on the server. Times are UTC.

| When | Command | What it does |
|---|---|---|
| Daily 05:00 | `verify_ats_jobs` | Confirms imported roles against each employer's own hiring system (ATS). |
| Daily 06:30 | `send_daily_signal --send` | Emails `OWNER_REPORT_EMAIL` yesterday's numbers against the 7-day average, with alerts. |
| Daily 08:15 | `send_employer_monthly_reports --send` | Sends each claimed organization its stats every 25 days or more, with a $49 feature link. |
| Monday 08:00 | `send_employer_offer --send --limit 25` | Sends the next 25 unclaimed employers with apply clicks their numbers and the offer. |
| Thursday 09:00 | `send_auto_roundup --send` | Sends the weekly roundup from roles verified open that morning, unless an issue already went out this week. |

To send a hand-edited issue instead, send it with `send_roundup` before Thursday 09:00. The automatic one then skips that week.

## Monday review

Run on production and paste the numbers into the week's row below.

- `reconcile_revenue --expected-account <stripe account id> --mode live --days 7 --apply`, then `growth_report --account <stripe account id>`: cash collected.
- `consumer_funnel_report`: signups, Scout and Pro funnel.
- `employer_outreach_report`: employer offers, claims and purchases.
- The newsletter audience admin page: confirmed weekly subscribers.

| Week of | Collected | Paying employers | Weekly subscribers | Offers sent | Notes |
|---|---|---|---|---|---|
| 2026-11-09 | $109 lifetime | 1 | 8 | 0 | Baseline |
