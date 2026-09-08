# White strict whole-game raw follow-up repair v4b-r6

2026-09-08 prospective correction of r5 registry admission. The r5 protocol required strict
whole-game Soccer YES3 / direct-sport2 identity, but r5 enrolled every league-accepted event,
including explicit child events. A verified operational snapshot contains 142 empty-slot child
entries and 15 true whole-game anchors. The child queue exhausted the 20 follow-up cap; this is
a collection defect, not evidence that the 42-second network budget itself must be relaxed.

Keep the v4b primary schema and existing entry/stop/episode/terminal economics unchanged. Keep
the shadow.db schema/application ID/data contract, cap20, network42/cycle50 seconds, and failure
publication policy unchanged. All r5 raw cycles, metadata, books and payloads remain immutable.
Do not convert historical FAILED runs to SUCCESS or merge old and new source cohorts.

New explicit child observations remain raw discovery evidence with state DISCOVERY_ONLY and
zero required quote targets. They are not recursively followed after leaving the live sweep.
For existing pending rows whose slots_json is empty, inspect only the indexed first_run_id ×
event_id raw observation. Change the mutable working state to DISCOVERY_ONLY only when that
exact observation proves a parentEventId. Append the reason, old/new state, source run/event,
parent ID and original event-JSON SHA to this run's raw event. Commit this correction together
with the new raw publication; never rewrite the source evidence. No historical table scan or
new schema/index is required. Missing first evidence is retained, not guessed to be a child.

A nonempty token anchor is never removed by this correction. A top-level event with a missing
triad or temporarily missing metadata remains tracked and may recover. A newly observed valid
whole-game identity remains eligible for raw follow-up regardless of strategy entry or price.
Current child metadata remains observable, while expected_tokens counts only required token
slots. expected_events counts published metadata rows; required_events and discovery_only_events
explicitly separate quote targets from child discovery/reclassification annotations.

The existing r5 parent/source/hash/receipt/clock/fee/terminal rules still apply. Metadata received
after a book cannot prove prior OPEN status. Actual remaining-budget or API failures remain
FAILED/incomplete. This correction changes observation health and does not choose parameters,
claim profitable trades, increase live exposure, or recover missing historical quotes.

Terminal publication is also reconciled separately from the working queue. A new terminal
proof remains in its raw event, but the working state is TERMINAL_PENDING_PUBLICATION until
the next cycle checks that exact raw run and parent run. Require raw PUBLISHED, one parent
STARTED plus one SUCCEEDED, no FAILED, matching config/source/job, ordered timestamps before
the current cycle, and the exact terminal event/payout mapping. Then use RESOLVED_CONFIRMED,
which is excluded from future reconciliation scans. Missing/failed publication requeues the
anchor as WAIT_RESOLUTION with append-only reconciliation details in the new cycle stats.
Legacy r5 RESOLVED candidates receive the same check. Any same-cycle API/budget incompleteness
keeps all newly terminal candidates in WAIT_RESOLUTION. Book errors remain errors; a terminal
fact does not turn a failed quote into a successful quote or rewrite parent economics.
