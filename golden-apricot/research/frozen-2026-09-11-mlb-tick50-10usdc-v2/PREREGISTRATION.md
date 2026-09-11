# Golden Apricot MLB Tick50 $10 A/B v2

- Universe: MLB whole-game direct HOME/AWAY two-team moneyline only.
- Entry signal: the first durable common valid HOME/AWAY snapshot is tick0. In `[50,52]`
  elapsed minutes, select the midpoint favorite with no price-band filter.
- Candidate evidence remains the baseline exact `$5` full-depth ask. The live target is `$10`;
  the same fresh book must fill the entire target, otherwise the existing adaptive ladder may
  reduce once to `$5`. Submit one atomic FOK BUY only.
- Eco A `apricot-live-eco-mlb-tick50-hold-v1`: resolution hold, no TP/SL.
- Fruit B `apricot-live-fruit-mlb-tick50-tp99-v1`: full-holding bid VWAP 0.99 TP,
  otherwise resolution.
- The only A/B treatment remains early 0.99 exit. Entry time, selector, target notional,
  universe, cadence and execution rules are common.
- The `$10` target is frozen only for these two MLB jobs. A future NFL/NBA/NHL or other sport
  runtime requires its own simulation, preregistration and amount.
- Experiment capital is `$100` per account, preserving the 20% economic drawdown fraction after
  doubling the per-entry target. Event/account/cycle limits and confirmed fill/fee reconciliation
  remain unchanged.
- Scale evidence: the prior 112 resolved MLB `$5` replay returned +5.83% for A and +6.37% for B;
  both chronological halves were positive. The 2026-09-11 refresh must retain exact source
  checksums and report `$10` full-depth availability before deployment.
- This is a prospective minimum scale step. Do not infer that another sport should use `$10`.
