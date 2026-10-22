# Linked Scout funnel and personalized samples

This change is built locally; activating it requires an application release and
migration `0079_scout_flow_events`. The migration changes Django event choices
only: its generated SQL is a no-op, with no data rewrite or schema change.

The example previews offer **Personalize this search** as the primary action,
with an editable brief on the same page. Visitors can still choose **Use this
exact search**. After editing, **Continue to my free Scout** takes them through
signup to a prefilled **Confirm your search** form. **Find and email my matches**
explicitly requests the one-time email. Signup and GET requests do not create or
email a Scout. Pro users retain recurring controls.

Each flow receives a random 128-bit identifier, stored in a session-bound map.
The identifier grants no access to user data. It carries neither an email address,
IP address nor raw session key and is not sent to GA4. Session rotation after
signup retains the map. Flow URLs and form fields are accepted only when the
identifier belongs to the current session and account. The map holds at most
10 flows. Flows expire after 24 hours and expired entries are discarded on the
next map write; stored-session cleanup follows Django session expiry. The pending
signup association expires after 30 minutes.
Separate tabs preserve their own flow parameter. Losing cookies or switching
devices can leave stages unlinked; the report never guesses those joins.

`scout_flow_id`, `scout_source`, `sample_origin` and `personalized` metadata link:

- Existing preview, signup handoff, account-created, Scout-created and initial
  email/empty-result events.
- New `scout_personalized` events for actual brief changes, with refreshes ignored.
- New `scout_form_viewed` events once per flow, including direct visits with no
  campaign source, only when the form is actually available.
- New `scout_submit_started` events for authenticated POST requests received.
- New `scout_submit_result` outcomes: `created`, `invalid`, `blocked`, `duplicate`
  or `error`. Invalid outcomes store field names, not values or error messages;
  unexpected errors store the exception class, not its message.

Email signup uses the validated Scout `next` destination; social signup uses
allauth's `sociallogin.state.next`. Generic signup, a different destination,
an expired handoff, a foreign session or a different account cannot inherit an
old pending Scout attribution. Existing campaign attribution remains separate.

Run the read-only report after release:

```
/opt/easyclaw/exec-active /app/.venv/bin/python /app/manage.py scout_funnel_report --days 7 --json
```

Its cohorts are search flows whose **first** linked preview occurred within the
rolling report window, with outcomes observed through report time. It separates
anonymous and already-authenticated entry, groups by original entry source, and
counts distinct flows per stage and individual submit outcomes. Form flows with
no observed preview are reported separately, rather than counted as preview
conversions. It exports no
briefs, flow IDs, account identifiers or session keys. Already-authenticated users
can legitimately skip signup stages. Repeated previews, invalid retries and
refreshes are not additional people.

Legacy events without flow IDs remain explicitly unlinked. They are not backfilled
into a fabricated cohort. Up to 5,000 preview-event rows are inspected, with a
`capped` flag if this bound is exceeded. Anonymous session loss, client-side
validation that prevents a POST, bot activity, internal testing and different time
available to convert remain limitations. The report does not prove why someone
left, establish a causal conversion lift or verify paid revenue.

Validation includes the mobile sample → edit → preview → real local email signup
→ final confirmation → one mocked email → refresh path, social-signup state,
session rotation/isolation/expiration, account switching, generic signup,
validation/blocking/error/duplicate outcomes and report boundaries. No production
accounts or real emails are created by those tests.

## Mobile continuation experiment — 19 October 2026

A compact next-step action now sits above the preview results on phones and tablets.
Personal searches offer **Email my matches**, with the account and confirmation steps
explained beside it. Samples offer **Make this search yours** and empty previews
offer **Adjust my search**. Desktop retains the existing results/sidebar layout.
The links use the existing session-bound signup handoff and explicit final POST;
neither viewing a preview nor creating an account sends an email.

This addresses verified button visibility, not a proven cause of abandonment.
Measure linked anonymous preview → signup → confirmed Scout cohorts after release;
exclude internal/browser test sources and retain session-loss caveats.
