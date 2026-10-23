# Weekly newsletter operations

The canonical weekly audience is `RoundupSubscriber` (admin: **Newsletter audience**).
`UserProfile` is account information; `SeekerProfile` is optional job-matching data.
Neither a matching profile nor a legacy checkbox grants weekly newsletter permission.

Audience states are weekly subscribed, awaiting email confirmation, needs weekly
consent, monthly list only and unsubscribed. Authenticated preference changes require
a verified account address; unverified addresses use the public rate-limited confirmation
flow. Public signup always needs a deliberate confirmation POST. GET scanners cannot
confirm. Unsubscribing through old or new newsletter links suppresses all newsletter
lists and onboarding marketing. Job Scout subscriptions remain independent.

The older footer promises monthly product updates. Importing its records does not
upgrade that promise to weekly mail. Legacy account checkboxes may be old defaults;
they are retained as provenance and require fresh consent. No unsolicited reconfirmation
campaign is sent. Newly created monthly records appear automatically in the audience.

## Reconcile and inspect (no email)

Use `exec-active` on the VPS, never the mutable checkout:

```
/opt/easyclaw/exec-active /app/.venv/bin/python /app/manage.py reconcile_newsletter_audience
/opt/easyclaw/exec-active /app/.venv/bin/python /app/manage.py reconcile_newsletter_audience --apply
/opt/easyclaw/exec-active /app/.venv/bin/python /app/manage.py roundup_report
```

Import lowercases/deduplicates addresses, records legacy origins and propagates opt-outs.
A newer explicit weekly confirmation can supersede an older opt-out. Preview never
writes, apply is idempotent, and neither mode sends or prints addresses. Reporting
separates all audience records from active weekly subscribers; do not sum legacy lists.
Account deletion removes the canonical audience and its delivery records too.

## Weekly editorial run: Thursday 10:00 Europe/Oslo

The Codex weekly newsletter automation prepares and reviews public employer sources
before sending. It operates separately from paused employer outreach and social posting.
It must stop and report a blocker if it cannot verify sources or provider configuration.

1. Reconcile the audience and inspect aggregate `roundup_report`.
2. `prepare_roundup --output-dir /tmp/roundup-YYYY-Www` produces a private draft;
   it never sends and never represents catalogue data as employer verification.
3. Verify every selected employer source, availability, hiring location and stated pay.
   Remove closed, unpaid or unsuitable listings. Fill the review fields with the actual
   check timestamp and truthful eligibility/pay notes. Include climate, global-health,
   AI-safety and early-career picks when supported by current sources.
4. Review the rendered HTML/text, mark the exact current ISO-week manifest `approved`
   and calculate its SHA-256. Source checks expire after24 hours. A changed or expired
   listing, wrong week, missing review or hash mismatch blocks sending.
5. Preview `send_roundup --manifest PATH`; then send with `--send --approve-sha256 HASH
   --limit 100`. Repeat only for remaining eligible recipients after inspecting results.
   Topic preferences are respected. No topic match means no email or consumed claim.
6. The unique subscriber/week delivery claim prevents duplicate sends, including from
   overlapping runs. Unknown provider outcomes are never automatically retried or
   sent through another provider. Provider acceptance is not inbox delivery.
7. Record aggregate audience, accepted/unknown/suppressed/no-topic counts and issue
   evidence; notify only for an actionable failure or meaningful change. Never publish
   addresses, unsubscribe tokens or private account information.

`send_weekly_digest` is now a compatibility entrypoint to the same reviewed sender and
ledger. Its default is a count-only preview; old recipient overrides cannot bypass
consent. Do not schedule the old personalization path as a second newsletter sender.

## Migration0086

Adds two consent/provenance columns to the small existing roundup table and descriptive
admin metadata. Database-level defaults retain insert compatibility with the previous
release. It does not grant legacy consent, rewrite user preferences, send email or
remove records. Deploy only through checked GitHub main with reviewed migration
permission, then run the explicit reconciliation command. Rollback leaves additive
columns in place and never reverses subscriber opt-outs.
