# Golden Black frozen paired protocol — 2026-08-20

- Contract: `sports-resolution-paired-v1`
- Entry window: `[2026-08-20T00:00:00Z, 2026-09-19T00:00:00Z)`
- Follow-up cutoff: `2026-10-19T00:00:00Z`
- Cadence: 5 minutes
- Population: Gamma `/events/keyset`, sports tag, open events, endDate `(0h,6h]`, event and
  market liquidity `>=10,000`, cumulative volume `>=5,000`; strict binary and accepting CLOB only.
- Pagination: keyset, limit 500, max 4 pages, terminal cursor required.
- Arm B: first exact `$5` ask VWAP in `[0.92,0.93]`.
- Arm A: first exact `$5` ask VWAP in `[0.94,0.95]`.
- Exit: unique CLOB winner at closed resolution; no target and no stop.
- Costs: source fee schedule, fallback sports taker rate 0.05; +1¢ adverse sensitivity.
- Unit: one episode per `condition_id × token_id × threshold`; event clustering for inference.
- Missing depth, label, cursor, or clock evidence is censored/error, never imputed.
- 24h/7d reviews are collection health only. No threshold selection before the 30-day window ends.
- Promotion gate: arm evaluable `>=300`, unique events `>=200`, resolution coverage `>=90%`, exact book
  coverage `100%`, and event-cluster cost-adjusted interval lower bound `>0` in a later untouched cohort.
- Accountless only: credentials and `--live` are forbidden before DB/log/network construction.

The historical screen selected these two thresholds. It is not part of the prospective outcome sample.
