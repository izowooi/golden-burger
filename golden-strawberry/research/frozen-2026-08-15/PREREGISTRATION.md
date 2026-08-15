# Golden Strawberry / Last Mile — frozen preregistration

Frozen at `2026-08-15T02:00:00Z`. This document defines the `last-mile-v1`
research cohort before its first eligible entry observation. Amendments must use a new frozen
directory, source digest, config hash, and independent cohort; this file is never rewritten after
outcome inspection.

## Research question

For every source-tradable Polymarket outcome token, does its first sampled upward interval crossing
of a high probability threshold support a hypothetical `$5` executable-ask entry followed by an
executable-bid path and terminal Gamma payout? The claim is falsified when the frozen primary policy
does not survive its predeclared health, sample-size, path, resolution, and independent-cluster gates.

This is accountless, public-data, archive-only research. It cannot place or manage orders, hold an
account, or represent the displayed-book walk as an actual transaction. Missing evidence is censored,
never replaced by a midpoint, last observation, forward value, or inferred terminal value.

## Frozen clock and source envelope

- Entry window: `[2026-08-15T02:00:00Z, 2026-08-22T02:00:00Z)`.
- Follow-up collection ends: `2026-09-21T02:00:00Z`.
- Cadence: 10 minutes; runtime job: `strawberry-shadow-one` only.
- Data contract: `last-mile-v1`; lifecycle: `archive_only`; mode: simulation only.
- Gamma: complete `/markets/keyset` cursor sweep, page size 100, at most 500 pages,
  `closed=false`, `include_tag=true`, `liquidity_num_min=0`, `volume_num_min=0`.
- No liquidity, cumulative-volume, 24-hour-volume, category, sport, market-type, or horizon gate is
  allowed. Eligibility is limited to source tradability: active, not closed, order book enabled,
  accepting orders, and aligned non-empty outcome/token/probability arrays.
- Binary, multi-outcome, negRisk, sports, and non-sports markets remain in the envelope. Event ID and
  event cluster are preserved. Sports classification uses `gamma-fields-tags-v1` and is a later
  stratum, never an eligibility rule.
- Liquidity, cumulative volume, 24-hour volume, end date, category, tags, negRisk, and outcome type
  are archived for predeclared descriptive strata.

The full Gamma census is published atomically only after a terminal, forward-only cursor. A partial,
repeated-cursor, malformed, or page-budget-exhausted attempt leaves request/run failure evidence but
no published sweep. Every complete sweep stores normalized full-membership gzip, page lineage, raw
gzip payloads, local source-receipt clocks, and SHA-256 values.

## Entry episodes and censoring

Entry thresholds are `0.90`, `0.92`, `0.95`, and `0.97`. A threshold episode requires all of:

1. the prior Gamma probability is strictly below the threshold and the current value is at or above;
2. prior and current rows have the same token and condition;
3. the local receipt gap is greater than zero and no more than 25 minutes;
4. the current receipt is inside the frozen entry window; and
5. no earlier episode exists for that token and threshold.

An initial observation at or above a threshold is `LEFT_CENSORED`, not an entry. A gap over 25 minutes
is `GAP_CENSORED`. Every sampled crossing is an interval observation between two source receipts: a
jump from below several thresholds to above them creates separately labeled threshold episodes but
does not prove continuous passage through any intermediate value. Resolution from below a target is
stored as a resolution jump, not a sampled target crossing.

Only newly crossing tokens and tokens belonging to unresolved executable episodes are requested from
the public CLOB `/books` endpoint. Each token has explicit observed, missing, malformed, empty, or
error evidence and a linked raw gzip response. A hypothetical entry is executable only when the
returned ask levels cover exactly `$5`; its ask VWAP, fixed shares, best ask, spread, and complete
displayed depth are archived. Source-provided fee-rate, tick-size, and minimum-size metadata at the
crossing are preserved as evidence, not assumed when absent. Insufficient ask depth is terminally censored for that episode and is
never retried as a new threshold episode.

Every cycle stores an executable-bid walk for the fixed episode shares, or an explicit source/depth
censoring reason. First sampled stop and target threshold events are append-only observations; old
path rows are never mutated. Closed-market Gamma lookups by condition archive a winning outcome and
token payout `0` or `1`, or an explicit missing, malformed, unresolved, or error result. A target price,
including `0.99`, is not terminal resolution.

## Frozen primary and sensitivity policies

The only primary policy is:

- entry threshold `0.95`;
- stop threshold `0.85`; and
- no price target: otherwise hold until a proven terminal Gamma payout.

The `0.90`, `0.92`, and `0.97` entry thresholds; `0.80` and `0.90` stops; and `0.98`/`0.99`
price targets are sensitivity-grid dimensions only. The analyzer reconstructs every valid combination
of entry `[0.90, 0.92, 0.95, 0.97]`, stop `[none, 0.80, 0.85, 0.90]` where stop is below entry, and
target `[none, 0.98, 0.99]` where target is above entry. Same-observation threshold ties use
stop-before-target ordering. A stop and terminal resolution observed in the same poll are explicitly
ambiguous and are conservatively ordered stop-before-resolution, never in the favorable direction.

All values are hypothetical and use entry ask VWAP followed by exit bid VWAP or the proven terminal
payout. The report shows gross results plus separately preregistered `10.4 bps` and `72.5 bps`
round-trip cost stresses. These are stress scenarios, not claims about an exact fee unless the source
metadata independently proves one. No midpoint substitution or forward completion is permitted.

## Dated capacity estimate

A no-filter design probe at `2026-08-15T00:xxZ` returned 32,132 markets over 322 pages in 121.39
seconds. A representative 100-market page was 841,325 raw bytes and 54,532 gzip bytes. At that point,
the raw-page estimate was about 16.7 MiB per sweep and 16.5 GiB for 1,008 ten-minute sweeps before
indexes and other evidence. These measurements justify the 500-page safety budget and external-volume
preflight; market count and byte rate are dated estimates, never collection contracts. Health reports
must replace them with observed growth and stop-threshold forecasts.

## Health and interpretation gates

The analyzer uses an immutable read-only SQLite connection and an explicit UTC half-open range. It
reports expected 10-minute slots, successful coverage, duplicate/off-slot runs, p95/max runtime, raw
linkage, `quick_check`, storage growth/forecast, crossing and episode CLOB coverage, path coverage,
resolution coverage, and left/gap censoring. It also reports the frozen strata and full sensitivity
grid without selecting a winner.

A one-week report is `HEALTH_ONLY` if collection evidence is unhealthy, otherwise
`PILOT_UNDERPOWERED` until all of these minimums hold:

- at least 50 executable episodes;
- at least 30 resolved independent event clusters;
- at least 90% episode-path coverage; and
- at least 90% resolution coverage.

Even when all four hold, the strongest label is `PILOT_CANDIDATE`. It is not a profitability claim,
parameter winner, or deployment approval. The primary policy still requires a separately frozen,
healthy 30-day out-of-sample cohort. Parameters may not be selected from this one-week cohort and then
reported on the same evidence; any selection starts a new preregistered cohort.

## Invalidating evidence conditions

- non-terminal or repeated Gamma cursor, incomplete membership, or non-atomic publication;
- missing source receipt/run/request/raw SHA linkage;
- credentials, live mode, account/order-capable code, or a lifecycle other than `archive_only`;
- midpoint, forward value, assumed price path, or target-as-resolution substitution;
- mutation/deletion of append-only evidence, except the labeled latest-state crossing cache;
- missing per-cycle path censoring for an unresolved executable episode;
- mixing config/source/job cohorts; or
- storage guard bypass, automatic evidence thinning, or untrusted workspace identity.

These conditions produce an evidence failure, not support for or against the market hypothesis.
