# Queue Echo v3 data contract

- Contract identifier: `queue-echo-v3`
- SQLite schema version: `3`
- Schema profile: `queue-echo-v3-sqlite-v3`
- Analyzer: `queue-echo-analyzer-v3`
- Cohort key: `config_hash × strategy_source_digest × mode × job_name`

Each v3 runtime writes only `data/<v3-runtime>/trades_sim.db`. The external-v2 runtime directories
and schema-1 `queue-echo-v1` databases are immutable legacy evidence and are never opened for v3
initialization or migration.

## Durable coordination evidence

- `cycle_slot_claims` has one immutable row per runtime × scheduled slot.
- `cycle_slot_events` records accepted, duplicate and late invocation dispositions.
- `followup_claims` has one immutable first-follow-up identity per research case.
- `followup_claim_leases` appends ownership generations only when an unstarted lease is stale.
- `followup_request_starts` has at most one durable logical request start per case claim.
- `followup_attempts` has at most one terminal disposition per case claim.

The append-only evidence tables reject UPDATE and DELETE. Coordination recovery appends a new lease
or a terminal censoring row; it never rewrites an earlier claim.

## Source-role semantics

`orderbook_token_attempts.attempt_role` and `orderbook_snapshots.snapshot_role` are either
`UNIVERSE` or `FOLLOWUP_ONLY`. A token can have both roles in one run. UNIVERSE pairs are requested
as atomic YES/NO units. FOLLOWUP_ONLY quotes are independent outcome requests and are excluded from
UNIVERSE coverage denominators.

`EMPTY_BOOK` means the normalized public response explicitly contained no bid and no ask levels.
It is distinct from `MISSING`, `MALFORMED`, `ERROR`, normalized book availability and quote
eligibility. None of these states is imputed as zero depth.

## Terminal evidence

Every STARTED run owns its lifecycle range by STARTED timestamp and ends in exactly one SUCCEEDED or
FAILED event. Terminal details include elapsed duration, 225-second cooperative budget, 240-second
hard limit, 30-second network margin and terminal phase/reason. Analyzer runtime/deadline gates use
both SUCCEEDED and FAILED terminal durations.

The canonical SQLite busy timeout is 10 seconds so the three post-network durable phases fit inside
the 30-second cooperative margin; a deployment must not raise it without a new cohort.

Follow-up terminal statuses are `QUOTE_COMPLETE`, `SOURCE_MISSING`, `EMPTY_BOOK`, `INVALID_QUOTE`,
`WINDOW_EXPIRED`, and `STALE_REQUEST_UNKNOWN`. The last status proves a first logical request was
durably marked before a crash but its quote disposition was not atomically published; recovery
censors it and never sends a replacement first request.
