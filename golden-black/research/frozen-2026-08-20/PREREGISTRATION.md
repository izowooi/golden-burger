# Golden Black frozen paired protocol — 2026-08-20

- Contract: `sports-resolution-paired-v1`
- Entry window: `[2026-08-21T00:00:00Z, 2026-09-20T00:00:00Z)`
- Follow-up cutoff: `2026-10-20T00:00:00Z`
- Cadence: 5 minutes
- Population: Gamma `/events/keyset`, sports tag, open events, endDate `(0h,6h]`, event and
  market liquidity `>=10,000`, cumulative volume `>=5,000`; exactly two aligned labels/prices/tokens,
  explicit boolean `negRisk`, and accepting CLOB only. Named moneyline (`negRisk=false`) and Yes/No
  proposition (`negRisk=true`) are retained as separate strata.
- Pagination: keyset, limit 500, max 4 pages, terminal cursor required.
- Arm B: first exact `$5` ask VWAP in `[0.92,0.93]`.
- Arm A: first exact `$5` ask VWAP in `[0.94,0.95]`.
- Primary payoff: unique CLOB winner at closed resolution; no early target.
- Exit sensitivity policies: `HOLD_TO_RESOLUTION`, `STOP_0.80`, `STOP_0.70`, `STOP_0.60`.
- Stop trigger: first observed best bid at or below the stop. Execution uses the full displayed bid
  depth for the remaining entry shares; trigger price is never imputed as fill price.
- Gap/partial contract: persist prior/trigger bid, actual VWAP, stop gap, filled/remaining shares,
  fees and every retry. Once triggered, retries continue until the counterfactual position is fully
  exited or the unresolved remainder receives terminal payout.
- Costs: source fee schedule, fallback sports taker rate 0.05; +1¢ adverse sensitivity.
- Unit: one episode per `condition_id × token_id × threshold`; event clustering for inference.
- Missing depth, label, cursor, or clock evidence is censored/error, never imputed.
- Stop values are a frozen sensitivity grid, not selected winners. No policy selection before the
  30-day entry window and resolution follow-up are complete.
- 24h/7d reviews are collection health only. No threshold selection before the 30-day window ends.
- Promotion gate: arm evaluable `>=300`, unique events `>=200`, resolution coverage `>=90%`, exact book
  coverage `100%`, and event-cluster cost-adjusted interval lower bound `>0` in a later untouched cohort.
- Accountless only: credentials and `--live` are forbidden before DB/log/network construction.

The historical screen selected these two thresholds. It is not part of the prospective outcome sample.

Pre-entry-window clarification (2026-08-20): build #1 confirmed that the implemented population was
the aligned two-outcome sports universe (47 named moneylines and 13 Yes/No negRisk propositions),
while the prose said “strict binary.” No episode opened before the frozen start. The wording and
normalized `neg_risk` field were corrected before `2026-08-21T00:00:00Z`; thresholds, clocks,
notional, stop grid and raw-payload contract did not change.
