# UX Outcomes Review: 2026-10-03

## Conclusion

The recent fixes demonstrably remove search/filter defects and improve listing
integrity. They do not yet establish better real-user relevance, retention, or
paid conversion. A real user's negative feedback exposes a matching problem
that still reproduces with the current matcher.

## Evidence of Functional Improvement

The October 2 baseline audit showed that untouched salary bounds returned 384
Python-search results versus 838 without those bounds. The fix removes this
unintentional exclusion of jobs with unknown salaries. Counts are historical
snapshots, not current inventory promises.

Other verified corrections include database-wide salary ordering, preservation
of multiselect filters during pagination, chronological fallback ordering,
country aliases, and technical-stack evidence checks. Final public and
authenticated audits passed 116 checks. The October 3 repair also rebuilt 3,667
stale embeddings, removed four confirmed-dead listings, and left no public
thin-description issues in its final snapshot. These demonstrate functional
correctness in tested scenarios, not satisfaction or conversion uplift.

## Behavioral Comparison

Read-only production queries at approximately 10:35 UTC on October 3 compared
equal 13.49-hour windows on either side of the first verified fixed-state
snapshot: October 2 at 21:05:40 UTC (23:05:40 Oslo). This is a snapshot boundary,
not an exact deployment timestamp. The earlier window may include initial
deployment activity; the later window spans additional October 3 repairs.
It is therefore not a clean old-version/new-version experiment.

| Measure | Earlier window | Later window |
| --- | ---: | ---: |
| Non-owner, non-staff accounts created | 12 | 6 |
| Scouts created | 4 | 1 |
| New Scouts with delivery records | 2 | 1 |
| Initial-email-sent events | 2 | 1 |
| Initial-empty-match events | 2 | 0 |
| Preview events | 7 | 23 |
| Preview events with zero matches | 0 | 4 |
| Checkout-started / checkout-created events | 0 / 0 | 0 / 0 |
| Payment-completed events | 0 | 0 |

The seven days preceding that snapshot contained 188 previews, including 49
empty previews (26.1%). The later window contained 4 empty previews out of 23
(17.4%). This is only a preliminary descriptive signal: briefs and traffic
composition differ, the sample is small, and anonymous QA/bot traffic cannot
be reliably excluded. Events are not unique visitors or a conversion cohort.

Known owner and QA/test-prefixed users and identifiable verification/test
sources were excluded from the window analysis. Existing tracking does not
provide enough evidence to connect all anonymous previews to later accounts,
or to estimate real-user search success, retention, and before/after latency.
No GA visitor totals or real-user performance measurements were obtained.

## Confirmed Remaining Relevance Problem

One user created a Scout overnight and marked all five delivered jobs not
relevant. The brief sought non-clinical behavioral-health/public-health work
using counseling and federal-grants experience, with explicit exclusions of
therapy, crisis counseling, and direct clinical treatment.

The saved interpretation reduced the broad brief to three role titles, left
experience unconstrained, and saved no exclusion keywords. Matching rewarded
generic compliance/training overlap and broad role-family evidence, returning
solar-program management, climate verification, technical specialist roles,
and grants accounting. These were shown with heuristic scores of 84-89.

A read-only replay of the saved preferences using the initial 30-day lookback,
without delivery/feedback exclusion, returned the same five titles in the
current system. Replaying the normal recurring path would exclude those
already-delivered/rejected jobs and would not be a valid comparison.

This is one user's feedback, not a representative satisfaction rate. However,
it is a concrete failure that the infrastructure fixes did not resolve. Across
all non-owner/non-QA stored delivery feedback, there are 12 negative marks and
one positive mark. Feedback is sparse and self-selected.

## Next Priority

Preserve explicit exclusions and seniority, distinguish required professional
domain from transferable skills, and validate shortlist relevance against the
full brief. Add this rejected brief to a privacy-safe regression/evaluation
set. Do not equate heuristic scores or nonempty results with user satisfaction.

Then compare a longer post-change period with matched weekday/hour cohorts,
using distinct-user activation, job-view/application actions, relevance
feedback, and paid checkout completion. Session-level cohort attribution and
explicit synthetic-traffic marking are needed for trustworthy preview funnel
measurement. No application code, real alerts, or subscriptions were changed
in this review; no user email or payment was triggered.
