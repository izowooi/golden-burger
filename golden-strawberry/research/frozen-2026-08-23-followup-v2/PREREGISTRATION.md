# Golden Strawberry / Last Mile Follow-up v2 — frozen preregistration

Frozen on `2026-08-23` after the v1 entry window closed. This document governs only the
`last-mile-clob-followup-v2` accountless epoch and never modifies or extends the identity of the
`last-mile-clob-v1` entry cohort.

## Purpose and boundary

The v1 entry window `[2026-08-15T04:00:00Z, 2026-08-22T04:00:00Z)` is closed. The v2 process imports
the deterministic set of v1 `EXECUTABLE` episode definitions and their terminal-resolution status
from one pinned, quiescent v1 SQLite database, then follows only episodes unresolved at the handoff.
It does not discover markets, traverse `/sampling-markets`, detect new crossings, add entries, tune a
parameter, or place an order.

The v1 source must have schema version `1`, data contract `last-mile-clob-v1`, runtime job
`strawberry-shadow-one`, and a latest successful cutoff at or after the entry-window end. The first
v2 seed stores the source sweep, config hash, strategy source digest, schema hash, row counts,
canonical episode/condition/threshold hashes, source file fingerprint, and cutoff. Every later run
must reproduce that anchor before HTTP or v2 publication. Any v1 file, sidecar, successful sweep,
episode, resolution, or canonical hash drift fails closed. The v1 database is opened only with
SQLite `mode=ro`; v2 never issues DDL, `ALTER`, `DELETE`, `VACUUM`, or any write against it.

## Frozen runtime

- Runtime job: `strawberry-shadow-one-followup-v2`.
- Data contract: `last-mile-clob-followup-v2`.
- Lifecycle/mode: `archive_only` / simulation only.
- Cadence: `7-59/10 * * * *` (10 minutes, unchanged from v1).
- Follow-up end: `2026-09-21T04:00:00Z`.
- CLOB `/books` batch limit: 250 tokens.
- Gamma resolution batch limit: 50 conditions.
- Public-request timeout/retry envelope: connect 3.05 seconds, read 30 seconds, four retries,
  exponential delay capped at 20 seconds.

No wallet, credential, signature, order, live mode, source sampling client, or concurrent writer is
permitted. The existing T7 APFS workspace identity, off-volume UUID pin, 100GiB free-space floor,
90% usage stop, and single-writer lock remain mandatory.

## Seed contract

Imported episodes are sorted and hashed canonical rows. Each row preserves the v1 episode ID,
originating sweep and decision lineage, condition/token/outcome identity, event cluster, entry
threshold and clock, fixed shares, displayed entry VWAP/depth/fee metadata, sports class, strata,
latest executable v1 bid path, and the set of already observed stop/target transitions. A condition
row preserves the latest successful v1 resolution observation and unique one-hot payout when proven.

Seed publication is atomic in the new v2 database. The original v1 raw pages, memberships, books,
levels, paths, and resolution payloads remain authoritative in v1 and are not copied or rewritten.

## Follow-up evidence

Each successful cycle requests one CLOB book per distinct unresolved token and one Gamma resolution
lookup per distinct unresolved condition. Multiple threshold episodes sharing a token share exactly
one book. A book is stored as one deterministic canonical gzip blob containing every validated bid
and ask level plus source metadata; row-per-level storage is forbidden.

The append-only v2 evidence consists of API attempt receipts, cycle provenance, token attempts,
compact full-book blobs, per-episode fixed-share displayed-bid paths, first stop/target transitions,
Gamma resolution observations, unique one-hot payout maps, data-quality issues, phase timings, run
events, and storage metrics. Missing or malformed books and resolutions remain explicit censoring.
Failed-run API receipts remain durable. Resolved conditions are excluded from every later request.

## Health-only interpretation

The combined analyzer reports v1 collection health and v2 follow-up cadence, source-anchor status,
book/path/resolution coverage, compact-blob integrity, DB integrity, phase runtimes, and storage
growth. It must not calculate profitability, select a threshold, recommend a parameter, or promote
the strategy to live operation. Missing v1 or v2 evidence is never synthesized.

The runtime SLA is a health instrument: a normal fixture must complete below 480 seconds so that the
10-minute cadence retains margin. An SLA miss is an instrument failure requiring collection or
storage remediation, not evidence about the trading hypothesis.
