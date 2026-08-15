# Golden Strawberry — Last Mile

## Hypothesis

Every outcome token ultimately pays `0` or `1`, but a sampled price above `0.95` does not prove that
it will win or that it cannot fall to `0.85`. Golden Strawberry actively records both favorable and
failure paths. It is an accountless counterfactual, never an execution bot.

The primary falsifiable policy is:

1. first sampled upward crossing of `0.95`;
2. hypothetical `$5` entry at the complete displayed ask walk;
3. hypothetical stop when the complete fixed-share displayed bid walk first reaches `0.85` or below;
4. otherwise hold until Gamma proves a terminal `0/1` payout.

Sensitivity entries are `0.90/0.92/0.97`, stops are `none/0.80/0.85/0.90`, and targets are
`none/0.98/0.99`, subject to stop < entry < target. They are frozen comparisons, not values to select
from the same week.

## Population and clocks

The source population is exactly one complete CLOB `/sampling-markets` cursor traversal, not a claim
about every market on Polymarket. There is no volume, liquidity, category, sport, horizon, negRisk,
or outcome-price filter. Source tradability is the only gate.

The entry window is `[2026-08-15T04:00:00Z, 2026-08-22T04:00:00Z)` at minute
`7,17,27,37,47,57`. Follow-up resolution evidence ends `2026-09-21T04:00:00Z`.

## Measurement contract

- The sampling endpoint token `price` is only a crossing signal; its microstructure meaning is not
  assumed.
- A first value already above a threshold is `LEFT_CENSORED`, not an entry.
- A gap above 25 minutes is `GAP_CENSORED`; every valid crossing remains interval-censored.
- Only a full `/books` ask walk creates an executable hypothetical entry.
- Every later observation walks bids for the original fixed shares; missing/depth failures are
  explicit censoring.
- `0.98/0.99` is not resolution. Terminal payout requires a closed Gamma market with one winner and
  the original episode token in the payout map.
- Stop precedes target in the same poll; stop precedes resolution when ordering is ambiguous.

Crossing-time Gamma enrichment archives liquidity, total volume, 24-hour volume, event IDs, category,
tags, and retrieval lag. It occurs after selection, never changes eligibility, and is never
backfilled. Unknown event clusters do not count toward independent-sample gates.

Sports classification is versioned and descriptive: explicit sport signals are `SPORTS`, nonempty
source taxonomy without a sport signal is `NON_SPORTS`, and absent taxonomy is `UNKNOWN`.

## Evidence and capacity

Raw source pages, compact full membership, request clocks/hashes/SHA values, crossing decisions,
books/levels, episodes, path observations, metadata, and resolution observations are append-only and
published atomically. Full parsed rows are retained only for nontrivial decisions; the mutable
`latest_outcome_state` table is a labeled crossing cache with raw-page lineage.

The predeployment probe saw about 12.5k markets and 25k tokens over 13 pages. Two full cycles took
5.5–6.3 seconds. The first DB was 31.7MB and the second added 6.46MB, planning roughly 6–7GB for week
one and about 35GB through follow-up. Runtime metrics, not this dated estimate, are authoritative.

## Decision gate

The one-week review can be only `HEALTH_ONLY`, `PILOT_UNDERPOWERED`, or `PILOT_CANDIDATE`. The
`--live` mode and live deployment are forbidden. Candidate status needs
50 executable episodes, 30 resolved known event clusters, and at least 90% metadata, path, and
resolution coverage. Even then, no profitability or live recommendation is allowed without a new,
healthy, frozen 30-day out-of-sample cohort.

The checksum authority is
`research/frozen-2026-08-15-clob/PREREGISTRATION.md` plus its `MANIFEST.sha256`.
