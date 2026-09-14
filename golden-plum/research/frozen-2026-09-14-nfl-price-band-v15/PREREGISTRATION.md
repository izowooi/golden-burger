# Golden Plum NFL price-band live A/B v15

## Prospective contract

- `polybot-king / plum-live-king-nfl-85-1m-v15`: TP `.85`.
- `polybot-queen / plum-live-queen-nfl-90-1m-v15`: TP `.90`.
- Both arms: NFL top-level whole-game direct two-team moneyline only, one
  complete two-token observation, unique midpoint leader, exact `$5` ask VWAP
  `[.70,.73]`, `$5` FOK BUY, entry VWAP minus `.12` full-position FOK stop.
- NFL has no soccer source-minute entry ceiling and no minute-65 exit. Missing
  source time is valid only for this direct two-team profile. Soccer keeps its
  `<60` entry and minute-65 full exit unchanged.
- Each sport owns a separate runtime, SQLite database, parameter profile,
  notional and cohort identity. A future NFL/NBA/NHL change cannot alter soccer.
- Confirmed economic loss limit is `$300` per arm. Manual wallet positions and
  wallet deposits/withdrawals are excluded.
- Entry begins at `2026-09-14T12:00:00Z` and remains enabled until an explicit
  operator stop or retune. Existing soccer and MLB rows are never migrated.

## Exploratory evidence

Verified `polybot-gold / plum-shadow-gold-nfl-1m-v1` pin
`20260914T105722Z` contains 15 resolved NFL games. At `$5`, 100 bps entry and
exit fee sensitivity, no source-time exit:

| arm | current 13-game cohort | historical 2-game cohort | combined |
|---|---:|---:|---:|
| TP `.85`, SL `.12` | 12 signals, `+$7.67932`, 10 positive | `-$0.58011`, 1 positive | `+$7.09921` |
| TP `.90`, SL `.12` | 12 signals, `+$6.70387`, 8 positive | `-$2.60194`, 0 positive | `+$4.10193` |

This is displayed-book counterfactual evidence selected from a small, clustered
opening-week sample. It is not actual fill P&L and does not authorize a notional
increase. Live begins at `$5`; the only treatment difference is TP.
