# Golden Strawberry / Last Mile — frozen CLOB preregistration

Frozen before collection at `2026-08-15T01:30:00Z`. This is the sole authority for the
`last-mile-clob-v1` accountless cohort. The earlier Gamma-keyset design was never deployed and is not compatible
with this cohort. Any change to source population, clocks, thresholds, cadence, or interpretation
requires a new frozen directory, data contract, source digest, config hash, DB, and entry window.

## Research question and falsification

For outcome tokens returned by the complete public CLOB `/sampling-markets` cursor traversal, does a
first sampled upward crossing of a high probability threshold support a hypothetical `$5`
displayed-ask entry, subsequent fixed-share displayed-bid path, and proven terminal payout? The
primary idea is falsified when entry `0.95`, stop `0.85`, otherwise terminal-resolution hold does not
survive the predeclared collection, sample-size, path, resolution, and independent-event gates.

The endpoint population is not described as every Polymarket market. Conclusions are limited to the
complete `/sampling-markets` population observed under this contract. The endpoint token `price` is
only a sampled crossing signal; it is not assumed to be an executable ask, last trade, or fill.

## Frozen clocks and cadence

- Entry window: `[2026-08-15T04:00:00Z, 2026-08-22T04:00:00Z)`.
- Follow-up ends: `2026-09-21T04:00:00Z`.
- Jenkins timer: `7-59/10 * * * *`; cadence 10 minutes, minute offset 7.
- Runtime job: `strawberry-shadow-one`; Jenkins job: `polybot-shadow-one`.
- Mode/lifecycle: simulation only / `archive_only`; no credential or order path exists.

The no-write design probe completed the source in 13 pages and 2.25 seconds. Two full local atomic
cycles, including SQLite publication, completed in 5.51 and 6.26 seconds. Five minutes would add
twice the source load without evidence that the extra samples justify it; 10 minutes leaves ample
retry and publication headroom while the 25-minute crossing-gap rule tolerates one missed slot.

## Source population and complete-census rules

Every entry-window cycle traverses `https://clob.polymarket.com/sampling-markets`, whose observed
source page size is 1,000, using its opaque `next_cursor` until the documented `LTE=` terminal. The
100-page cap is only a safety bound. A repeated cursor, non-object page, count mismatch, duplicate
condition, duplicate token, empty nonterminal page, or full 1,000-row terminal page invalidates the
cycle. Partial data is never published.

Eligibility is limited to source fields: `active=true`, `closed=false`, order book enabled,
`accepting_orders=true`, and at least two aligned unique token/outcome/finite `[0,1]` price rows. No
liquidity, total-volume, 24-hour-volume, sport, category, horizon, negRisk, or threshold filter is
allowed. Source membership, all raw gzip pages, receipt clocks, cursors, request hashes, and SHA-256
lineage are published atomically.

Sports classification `clob-fields-tags-v1` uses explicit game/sport fields and source tags. An
explicit sports signal is `SPORTS`; nonempty taxonomy with no sports signal is `NON_SPORTS`; absent
taxonomy is `UNKNOWN`. Classification is a descriptive stratum, never eligibility.

## Crossing, censoring, and entry evidence

Entry thresholds are `0.90`, `0.92`, `0.95`, and `0.97`. A threshold episode requires a prior sampled
token price strictly below and current price at or above the threshold, identical token and
condition, a positive receipt gap no greater than 25 minutes, current receipt inside the entry
window, and no previous episode for that token/threshold.

The first complete sweep is baseline only. A first value already above threshold is
`LEFT_CENSORED`; a reappearance or missed interval beyond 25 minutes is `GAP_CENSORED`. Every crossing
is interval-censored. A jump can create several labeled threshold episodes but never proves
continuous passage.

Only newly crossing tokens and unresolved executable episodes request public CLOB `/books`. A `$5`
entry exists only when displayed asks fully cover `$5`; fixed shares, ask VWAP, all book levels,
spread, depth, tick/minimum size, source fee metadata, raw payload, and SHA lineage are stored.
Subsequent cycles walk displayed bids for exactly those shares or store an explicit censoring state.
No midpoint, last-value, forward-fill, or zero-depth substitution is allowed.

## Metadata, resolution, and independent units

Gamma is not the crossing source. It is queried only after a crossing to snapshot liquidity,
cumulative volume, 24-hour volume, category/tags, end date, market ID, and event IDs, and later to
prove closed-market resolution. Enrichment request/receipt time and lag are immutable. Missing
metadata never blocks or creates an entry and is never backfilled from future values.

An event ID is the independent cluster. Missing event metadata is `UNKNOWN_CLUSTER` and is excluded
from the 30-resolved-cluster gate rather than counted condition-by-condition. A resolution is valid
only when Gamma reports one terminal winner, every payout is `0/1`, and every unresolved episode
token for the condition appears in the payout map. `0.98` or `0.99` is a sampled target, not
resolution. A jump directly to resolution is recorded without inventing target passage.

## Frozen policy grid

The sole primary policy is entry `0.95`, stop `0.85`, otherwise hold to proven terminal resolution.
Sensitivity dimensions are entry `[0.90, 0.92, 0.95, 0.97]`, stop
`[none, 0.80, 0.85, 0.90]` where stop is below entry, and target `[none, 0.98, 0.99]` where target is above
entry. Same-poll stop precedes target; same-poll stop and resolution are conservatively ordered
stop-before-resolution.

Returns use entry ask VWAP and exit bid VWAP or terminal payout. Gross results and separate 10.4bps
and 72.5bps round-trip stress scenarios are reported. These stresses are not exact fee claims.
Liquidity and volume thresholds may be proposed only as hypotheses from the frozen strata; no winner
may be selected and reported on this same cohort.

## Storage and append-only evidence

The complete raw census and compact normalized membership are append-only. Full parsed market/outcome
rows are appended only when a nontrivial crossing/censoring decision needs them. The sole mutable
table, `latest_outcome_state`, is a labeled crossing cache with raw page lineage and is not outcome
evidence by itself.

The dated full-source probe observed 12,555 markets, 25,110 tokens, 13 pages, 33.3MB raw and 3.83MB
gzip. The first optimized atomic DB was 31.66MB; an immediate second cycle added 6.46MB. A 10-minute
week therefore plans for roughly 6–7GB and the 37-day entry-plus-follow-up horizon for roughly 35GB,
before backup margin. These are planning measurements, not guarantees. Runtime enforces 100GiB free
space and a 90% filesystem stop; observed growth and forecast replace the estimate after deployment.

## Interpretation gates

Collection health requires at least 90% expected-slot success, complete cursor and membership/raw
lineage, no duplicate runs, bounded end-to-end runtime, one cohort, no HIGH/CRITICAL issues, and
separate crossing-book, candidate-metadata, path, and resolution coverage. The strongest one-week
label is `PILOT_CANDIDATE`, requiring all of:

- at least 50 executable episodes;
- at least 30 resolved known event clusters;
- at least 90% episode-path coverage;
- at least 90% resolution coverage; and
- at least 90% crossing-time Gamma metadata coverage.

Failed health yields `HEALTH_ONLY`; insufficient evidence yields `PILOT_UNDERPOWERED`. Even
`PILOT_CANDIDATE` is not a profitability or live-deployment claim. The primary policy requires a new
frozen, healthy 30-day out-of-sample cohort. This collector can never place an order.
