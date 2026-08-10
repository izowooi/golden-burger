# Golden Kiwi / Micro-Cascade — filtered-universe 30-day protocol

## Decision and window

This protocol replaces the cadence-invalid collection that began on
2026-08-06. Evidence from that deployment remains diagnostic only and must not
be merged into this experiment.

The new promotion collection is the UTC half-open interval:

```text
[2026-08-13T00:00:00Z, 2026-09-12T00:00:00Z)
```

Golden Kiwi remains research/simulation-only. Live execution stays blocked at
source level.

## Runtime incident being repaired

The previous four jobs independently fetched the same broad Gamma request
envelope every five minutes. A representative synchronized cycle fetched 267
pages and 26,654 raw markets per arm. The sweep alone took about five minutes,
causing queued builds, collapsed A/B/C/D offsets, off-schedule SUCCESS runs,
and snapshot gaps above ten minutes.

This protocol keeps the five-minute signal clock and narrows the request
envelope before pagination. It does not widen the accepted snapshot gap.

## Frozen Gamma request envelope

Every A/B/C/D source sweep must use all of the following:

```text
endpoint                  = GET /markets/keyset
closed                    = false
include_tag               = true
limit                     = 100
liquidity_num_min         = 20,000
volume_num_min            = 10,000  # cumulative Gamma volume
max complete-sweep pages  = 53
max raw markets           = 5,330
max sweep elapsed         = 120 seconds
```

`next_cursor` must be followed to completion. If completing the request would
exceed any budget, the run fails without publishing a partial sweep or
snapshot. A failed run is not converted into SUCCESS evidence.

The page and market budgets are one fifth or less of the incident envelope:

```text
floor(267 / 5)    = 53 pages
floor(26,654 / 5) = 5,330 markets
```

The cumulative `volume_num_min` request filter is not the same metric as
`volume24hr`. Client qualification rechecks cumulative volume, while the
strategy scanner separately enforces the frozen 24-hour entry gate.

## Frozen entry universe

The strategy-level entry controls remain unchanged:

```text
liquidity       >= 20,000
volume24hr      >= 10,000
YES probability = [0.20, 0.80]
resolution      >= 6 hours
spread          <= 0.02
```

The 2026-08-11 filter benchmark found 17 point-in-time strict candidates under
the existing entry contract. Adding cumulative `volume_num_min=10,000` kept
all 17. Raising the strategy's `volume24hr` threshold to 15k/20k/25k/50k
would have reduced the same snapshot to 15/13/12/8 candidates. Because runtime
is already solved by the request filter and the primary signal has a minimum
sample gate, `volume24hr` is not raised in this protocol.

A market that crosses into the filtered request envelope has no inferred
history. It must accumulate a new exact 3/5-step persisted lineage before it
can signal. Backfill, forward-fill, and observations from the invalid prior
cohort remain prohibited.

## Frozen arms and schedule

| Arm | Positive steps | Cumulative floor | Canonical job | UTC minute offset |
|---|---:|---:|---|---:|
| A | 3 | 0.01 | `kiwi-sim-a-3x1` | 0 |
| B | 3 | 0.02 | `kiwi-sim-b-3x2` | 1 |
| C | 5 | 0.01 | `kiwi-sim-c-5x1` | 2 |
| D | 5 | 0.02 | `kiwi-sim-d-5x2` | 3 |

Triggers remain `0-59/5`, `1-59/5`, `2-59/5`, and `3-59/5`. Concurrent builds
remain disabled. Snapshot gaps remain inclusive `[3,10]` minutes. A p95 cycle
runtime above five minutes, any off-schedule SUCCESS, or any duplicate-slot
SUCCESS invalidates promotion evaluation.

## Outcome and promotion gate

The primary arm remains B. The primary outcome remains event-equal
`exit_best_bid / entry_best_ask - 1`, where entry is the append-only
`raw_selected` snapshot ask and exit is the first valid direct condition quote
from +60 through +75 minutes. The direct follow-up lookup is not restricted by
the main request-envelope filters.

All prior statistical and evidence gates remain unchanged: at least 50
quote-complete primary signals, 30 event clusters, positive event-cluster
98.75% lower bound before and after 10.4bps stress, positive early and late
halves, at least 90% quote and cadence coverage, one cohort per arm, one shared
strategy source digest, and strict audit CRITICAL/HIGH equal to zero.

Passing permits only shadow-execution review. It does not authorize live
orders.

## Benchmark and external contract

The threshold-selection measurements are preserved in
`GAMMA_FILTER_BENCHMARK.json`. The API parameters and page limit are documented
by Polymarket:

- https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination
- https://docs.polymarket.com/api-reference/rate-limits
