# Golden Watermelon Live frozen pilot — 2026-08-24

## Provenance

- Predecessor collector: `golden-watermelon`
- Verified sources: `polybot-white/watermelon-white-1m-v3a` and
  `polybot-grey/watermelon-grey-5m-v3a`
- Live jobs: `polybot-cat/watermelon-live-cat-98` and
  `polybot-dog/watermelon-live-dog-99`
- Entry: `[2026-08-24T13:00:00Z, 2026-08-31T13:00:00Z)`
- Follow-up cutoff: `2026-09-07T13:00:00Z`
- Cadence: 5 minutes, non-concurrent
- Order notional: exact `$5` per accepted entry
- Cohort: `config_hash × strategy_source_digest × mode × job_name`

## Evidence used before freezing

White cadence coverage was 1,274/1,275 expected intervals with p50 about 59.85 seconds and p95
about 68.5 seconds. The verified cohort contained 48 five-league event observations: EPL 6,
Ligue 1 12, LaLiga 18, MLS 12, Bundesliga 0. Exact `$5` book coverage was 97.31%.

White hold-to-resolution outcomes were:

| threshold | resolved events | wins/losses | fee-net ROI |
|---:|---:|---:|---:|
| 0.95 | 5 | 4/1 | -16.21% |
| 0.96 | 5 | 4/1 | -17.85% |
| 0.97 | 5 | 4/1 | -17.85% |
| 0.98 | 3 | 3/0 | +1.906% |
| 0.99 | 1 | 1/0 | +0.382% |

The losing soccer event was Newcastle vs Liverpool; Newcastle YES was executable around 0.970
and resolved to 0. Grey was positive but did not observe that EPL loss, so its result is cadence
selection-biased. The sample is too small to claim optimality.

Stop replay showed 0.80 and higher triggers removed eventual winners. A 0.70 trigger was the least
harmful tested choice, but the loser gapped from the threshold region to an executable bid around
0.27. A stop threshold is not a guaranteed fill price.

## Frozen treatment

- Cat: exact `$5` ask VWAP `[0.98,0.999]`
- Dog: exact `$5` ask VWAP `[0.99,0.999]`
- Both: EPL, Bundesliga, Ligue 1, LaLiga, MLS only
- Both: top-level whole-match home/draw/away moneyline proposition, YES token only
- Both: explicitly in-play and not ended, game age `[0h,4h]`
- Both: first arm observation per condition; at most one open position per event
- Both: full-depth FOK BUY; exact order/fill/fee reconciliation before HOLDING
- Both: best-bid trigger `<=0.70`, full-holding bid walk, marketable FOK SELL
- Both: no TP, trailing, time exit, wallet-wide adoption or account-wide wind-down
- Exposure: total/event/new-per-cycle `20/1/20`, reentry cooldown 720h
- Gamma liquidity/volume gates: zero; exact executable CLOB depth is authoritative

If no result in a game reaches the assigned threshold, there is no forced bet. If more than one
result appears above threshold, the event fails closed. Manual wallet positions are out of scope.

## Review gates

- 24h: health only; no profitability or parameter selection
- 7d: unique-event and execution coverage; extend or remain inconclusive if sparse
- follow-up: fee-net event-clustered return, tail loss and stop shortfall
- Any CRITICAL/HIGH fill, fee, cohort, cursor, DB-integrity or manual-position contamination gap
  blocks profitability and scale-up claims
- Cat/Dog balance targets are operator expectations, not guaranteed outcomes or a decision rule

No post-start threshold, clock, league, stop or exposure amendment belongs to this cohort. A required
safety/evidence fix creates a new strategy-source digest and must be reported as a separate cohort.
