# Golden Strawberry — Last Mile strategy contract

## Status and falsifiable hypothesis

`last-mile-v1` is an accountless, archive-only simulation. It tests whether an outcome token’s first
sampled upward interval crossing of a high-probability threshold supports a hypothetical `$5`
displayed-ask entry, subsequent fixed-share displayed-bid path, and terminal Gamma payout. It does not
assume the token traveled continuously between sampled values and does not equate `0.99` with terminal
resolution.

The frozen primary policy is entry `0.95`, stop `0.85`, otherwise hold to a proven terminal payout.
Entry `0.90/0.92/0.97`, stop `0.80/0.90`, and target `0.98/0.99` variants are sensitivity-only. No
policy may be promoted from the one-week cohort without a new preregistration and independent cohort.

## Source population

Each 10-minute cycle completes Gamma `/markets/keyset` with page size 100, maximum 500 pages,
`closed=false`, `include_tag=true`, and both server numeric minima at zero. There is no client-side
liquidity, cumulative-volume, 24-hour-volume, category, horizon, binary/multi, negRisk, or sports gate.

Source tradability is the sole eligibility boundary:

1. `active=true`;
2. `closed=false`;
3. order book enabled;
4. accepting orders; and
5. outcome labels, token IDs, and probabilities are aligned, non-empty, and valid.

The catalog preserves event IDs and uses the first source event ID as an independent event cluster,
falling back to condition ID only when no event ID exists. `gamma-fields-tags-v1` classifies sports
from explicit sports fields, category, and source tags. Classification is archived with its version
and never changes eligibility. Liquidity, both volume clocks, end date, category/tags, outcome type,
and negRisk remain source-time strata.

## Crossing and episode identity

The entry clock is `[2026-08-15T02:00:00Z, 2026-08-22T02:00:00Z)`. A token/threshold crossing needs a
prior value below the threshold and current value at or above it, same token and condition, and a
positive source-receipt gap no longer than 25 minutes. There is one first episode per token and entry
threshold; no re-entry is possible.

An initial value at or above a threshold is `LEFT_CENSORED`. A candidate separated by more than 25
minutes is `GAP_CENSORED`. A jump may bracket several thresholds and create separately labeled first
episodes, but every one remains interval-censored. Resolution from below a target is explicitly stored
as a resolution jump without sampled target passage.

## Displayed-book counterfactual

The collector requests CLOB `/books` only for newly crossing tokens and unresolved executable episode
tokens. Missing, malformed, empty, transport-error, and insufficient-depth states are explicit and
never converted to zero depth or a price.

At crossing, asks must cover exactly `$5`. The archived evidence includes ask VWAP, fixed shares, best
ask, spread, all returned bid/ask levels and depths, raw gzip/SHA lineage, and source-provided
fee-rate/tick/minimum-size metadata. Every unresolved episode poll walks bids for those fixed shares or
stores an explicit censoring reason. First sampled stop and target observations are append-only.

Closed Gamma lookups by condition preserve the winning outcome, winning token, and all token payouts
as `0/1`, or an explicit missing/malformed/unresolved/error result. No midpoint, last-value, forward,
or target-as-resolution completion is allowed.

Gross values use entry ask VWAP and exit bid VWAP or terminal payout. The analyzer separately subtracts
preregistered 10.4 bps and 72.5 bps round-trip cost stresses. Source fee metadata is reported as
evidence but never generalized into an exact fee without source proof.

## Atomicity and lineage

Request attempts are append-only operational receipts. A Gamma traversal publishes no sweep unless it
reaches a terminal, forward-only cursor within 500 pages. On success, Gamma full membership, compressed
membership, raw page lineage, catalog/outcome state, crossing decisions, CLOB attempts/books/levels,
episodes, path rows, resolution observations, quality issues, and cycle stats publish in one SQLite
transaction.

All source and derived evidence tables have update/delete rejection triggers. The sole exception is the
clearly named `latest_outcome_state`, used only to compare the next Gamma probability. Cohorts are
`config_hash × strategy_source_digest × mode × job_name`; Git commit is provenance only.

## Frozen interpretation

The analyzer reconstructs valid entry × stop × target combinations. Within one sampled poll it uses
stop-before-target ordering. Stop and terminal resolution in the same poll are ambiguous and ordered
stop-before-resolution so the ambiguity is never resolved favorably.

Collection health includes expected slots, terminal run state, cursor/membership/raw linkage, duplicate
and off-slot runs, runtime p95/max, SQLite integrity, storage growth/forecast, CLOB crossing/episode
coverage, path/resolution coverage, and left/gap censoring. It reports counts by threshold, path event,
resolution outcome, sport, outcome type, negRisk, liquidity, and volume strata.

At least 50 executable episodes, 30 resolved independent event clusters, 90% path coverage, and 90%
resolution coverage are required before `PILOT_CANDIDATE`; failed collection health is `HEALTH_ONLY`
and insufficient samples are `PILOT_UNDERPOWERED`. `PILOT_CANDIDATE` still requires a frozen healthy
30-day out-of-sample confirmation and is neither profitability nor live-deployment approval.

## Capacity basis

The `2026-08-15T00:xxZ` no-filter probe measured 32,132 markets over 322 pages in 121.39 seconds. A
representative page compressed from 841,325 to 54,532 bytes, estimating 16.7 MiB raw gzip per sweep and
16.5 GiB across 1,008 week-one polls before indexes/other evidence. These are dated planning inputs,
not population contracts. Actual health uses observed growth and guard-stop forecasts.

The checksum-frozen authority is
`research/frozen-2026-08-15/PREREGISTRATION.md` and its `MANIFEST.sha256`.
