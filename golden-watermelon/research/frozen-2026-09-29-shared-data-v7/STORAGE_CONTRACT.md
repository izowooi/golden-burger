# Shared public-data storage contract

This contract changes physical storage while preserving Watermelon's research
data identity, economic meaning and original observation timestamps.

## Public bodies and publication

Only the explicit `golden-watermelon` table/column rules in
`polybot_observability.market_data_policy` permit public-body references. Preserve
the original TEXT/BLOB storage class, exact bytes, NULL, source-body SHA and
compressed/uncompressed byte counters. A local reference may be committed only
after durable acknowledgement from the shared writer. Failed local publication
can leave an unreferenced immutable public object; it cannot justify a dangling
reference or a successful partial cycle.

Raw response/attempt rows, timestamps, failures, original request identifiers and
source lineage remain intact. Shared content is not a new observation. Readers
must resolve the referenced bytes before JSON parsing, decompression, hashing or
replay. A missing or corrupt body is an evidence failure, not missing-as-zero.

## Public scalar levels and logical schema

New `orderbook_levels` price/size rows are stored as shared arrays after the
original main table's affinity, CHECK, UNIQUE and foreign-key constraints have
been validated within a temporary SAVEPOINT insertion that is rolled back.
The local `_market_data_level_groups` and `_market_data_level_bindings` preserve
snapshot identity, every original `level_id`, ordering and public-array reference.
The `_market_data_layout` contract identifies the owning strategy.

`main.orderbook_levels` preserves preexisting inline rows as historical data.
A connection-local TEMP VIEW named `orderbook_levels` combines those rows with
resolved shared arrays, retaining explicit primary keys and supporting the
existing JOIN, SUM and ORDER BY queries. SQLite's implicit `rowid` is not part of
this interface; readers must use `level_id` and the original natural keys.

Schema validation first calls `validate_level_layout` to verify the exact shared
auxiliary DDL. Only the object names returned by that successful validation are
excluded from the original logical schema SHA calculation. Unrecognized or
altered auxiliary objects remain errors. The original research tables, indexes,
append-only triggers, application ID, user version and logical schema fingerprint
remain authoritative. No arbitrary table-name prefix exemption is allowed.

## Historical conversion and evidence portability

Convert a fixed, verified original into a separate derivative database; do not
rewrite the original file or its frozen manifest. Preserve original public level
PKs, parent links, original and migrated DB SHA values, source/runtime/epoch,
source cutoff and conversion provenance. Verify private-row equality and the
logical row values after resolving shared references. Quarantine an original
only after the derivative and the original-to-derivative mapping are verified.

A SQLite snapshot alone is insufficient when it contains shared references.
Snapshot/sync/verify/pin must bind the database SHA to a verified payload closure.
Both body references and level-array references belong to that closure. Verify
restoration on the intended external storage root without an internal fallback.
This source epoch does not declare historical migration or operational deployment
complete; those require their own execution evidence.
