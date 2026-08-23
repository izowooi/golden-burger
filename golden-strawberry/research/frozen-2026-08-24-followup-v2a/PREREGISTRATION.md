# Golden Strawberry / Last Mile Follow-up v2a — frozen preregistration

Frozen on `2026-08-24` before the first successful follow-up database was created. Jenkins build
`#761` attempted the retired v2 rollout but produced no v2 database. This document therefore starts
the independent `last-mile-clob-followup-v2a` runtime/DB identity; it does not amend, migrate, reuse,
or overwrite either `last-mile-clob-v1` or the attempted `last-mile-clob-followup-v2` provenance.

## Purpose and immutable boundaries

The v1 entry window `[2026-08-15T04:00:00Z, 2026-08-22T04:00:00Z)` remains closed. v2a imports the
deterministic set of v1 `EXECUTABLE` episodes and their terminal state from the one canonical,
quiescent v1 SQLite database. It then follows only unresolved imported episodes. It never traverses
`/sampling-markets`, detects a crossing, creates an entry, tunes a parameter, places an order, or
writes to v1. v1 is opened only through SQLite `mode=ro`; no v1 migration, DDL, DML, `VACUUM`,
sidecar, or permission change is allowed.

The attempted v2 config, preregistration, runtime directory, and any future evidence discovered
under that identity remain separate historical provenance. v2a may create only its own new database.

## Frozen v2a identity

- Runtime job: `strawberry-shadow-one-followup-v2a`.
- Data contract: `last-mile-clob-followup-v2a`.
- Database: `data/strawberry-shadow-one-followup-v2a/trades_sim.db`.
- Lifecycle/mode: `archive_only` / simulation only.
- Cadence: `7-59/10 * * * *`; follow-up end `2026-09-21T04:00:00Z`.
- CLOB `/books` batch limit: 250 tokens; Gamma resolution batch limit: 50 conditions.
- Public request envelope: connect 3.05 seconds, read 30 seconds, four retries, exponential delay
  capped at 20 seconds, all further bounded by the shared cycle deadline.

No credential, wallet, signature, order, live mode, sampling client, candidate-metadata request,
concurrent writer, workspace wipe, or automatic DB repair is permitted. The T7 APFS identity,
off-volume UUID pin, workspace marker, 100GiB free-space floor, 90% usage stop, and nonblocking
single-writer lock remain mandatory.

## Seed and every-cycle source validation

The first manual deployment run is `FULL_SEED`. It may use a separate bounded 1,800-second
maintenance budget to scan and canonically import v1 episodes, condition status, and previously
observed threshold transitions. Seed publication is one transaction in the new v2a database.
If later public collection or successful-cycle publication fails, the imported seed remains
immutable but deployment stays in `FULL_SEED`: the next manual attempt recaptures and verifies the
same canonical source/seed under the maintenance budget. `PINNED_FAST` is allowed only after one
`FULL_SEED` cycle and its terminal `SUCCEEDED` commit atomically.

Every later run is `PINNED_FAST`. Before any public HTTP or cycle publication, every such run must:

1. reject v1 `-journal`, `-wal`, and `-shm` sidecars;
2. match the exact v1 canonical path, device/inode/size/mtime stat fingerprint;
3. match v1 schema hash/version, data contract, runtime job, experiment clocks, source config/source
   digest, latest successful sweep ID/cycle/completion/success cutoff, and prove no later published
   sweep exists;
4. verify the stored source-anchor SHA-256 from its canonical fields; and
5. reconstruct imported v2a episode/condition/threshold canonical rows, verify every row hash, and
   match exact counts, terminal count, aggregate seed hashes, and source-count anchor fields.

Any mismatch records a terminal failed run when the v2a audit store is usable, then fails closed
without network or cycle evidence. It is never repaired or reseeded in place.

## Deadline and publication transaction

Each public-data phase receives one cooperative monotonic deadline of 450 seconds. Every CLOB/Gamma
batch, retry decision, connect/read timeout, exponential sleep, and `Retry-After` delay must fit the
same remaining budget. A request or delay that cannot fit aborts immediately. A recurring
`PINNED_FAST` cycle must terminate strictly below the 480-second hard SLA, leaving time to persist a
clean `FAILED` event. The 1,800-second `FULL_SEED` maintenance budget is one-time and is not a
recurring cadence/SLA observation. Its network phase receives at most 450 seconds and is also capped
by the remaining 1,800-second maintenance deadline; the entire successful `FULL_SEED` must commit
strictly below that maintenance budget.

API attempt receipts and `STARTED`/`FAILED` events are durable failure evidence. Successful cycle
evidence is stricter: the cycle row, token attempts, canonical books, episode paths, first threshold
transitions, resolution rows, quality issues, all phase timings, successful-publication storage
metric, and terminal `SUCCEEDED` event commit in one SQLite transaction. Any failure after cycle rows
are staged rolls that transaction back. A failed run cannot leave path, resolution, or threshold rows
that alter later unresolved/transition state.

## Follow-up evidence and interpretation

Each successful cycle requests one CLOB book per distinct unresolved token and one Gamma lookup per
distinct unresolved condition. Threshold episodes sharing a token share one deterministic canonical
gzip full-book row. Missing/malformed books and resolutions remain explicit censoring. A unique
one-hot terminal payout is required, and resolved conditions are excluded from every later book and
resolution request.

The combined analyzer is health-only. It verifies v1 semantics, the v2a anchor and seed, compact blob
integrity, exact cycle coverage, resolution exclusion, append-only/foreign-key integrity, phases,
storage, and deadlines. The manual `FULL_SEED` run is reported as maintenance but excluded from the
recurring 480-second cadence/SLA denominator. Rollout cadence health begins at the first successful
natural on-slot `PINNED_FAST` run. No profitability calculation, threshold selection, parameter
recommendation, missing-evidence synthesis, or live promotion is allowed.
