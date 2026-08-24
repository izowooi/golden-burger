# Golden Watermelon Live 1-minute pilot v2a — 2026-08-24

## Provenance and amendment boundary

- Frozen decision timestamp: `2026-08-24T14:55:00Z`.
- Predecessor collector: `golden-watermelon`.
- Verified sources: `polybot-white/watermelon-white-1m-v3a` and
  `polybot-grey/watermelon-grey-5m-v3a`.
- Historical zero-opportunity live epoch:
  `watermelon-live-cat-98` / `watermelon-live-dog-99`, 5-minute cadence.
- Active live jobs: `polybot-cat/watermelon-live-cat-98-1m-v2a` and
  `polybot-dog/watermelon-live-dog-99-1m-v2a`.
- Entry: `[2026-08-24T13:00:00Z, 2026-08-31T13:00:00Z)`.
- Follow-up cutoff: `2026-09-07T13:00:00Z`.
- Cadence: 1 minute for both arms, non-concurrent per Jenkins job.
- Order notional: exact `$5` per accepted entry.
- Cohort: `config_hash × strategy_source_digest × mode × job_name`.

The historical 5-minute live DBs had nine successful cursor-complete runs per
job and zero eligible snapshots, trades, orders, fills, or open states at the
amendment cutoff. They remain immutable deployment evidence and are not merged
with v2a. The v2a runtime name and preregistration/source digest create a new
prospective evidence boundary.

## Evidence used before freezing

At source cutoffs `2026-08-24T14:45:46.728151Z` (White) and
`2026-08-24T14:42:34.475880Z` (Grey), daily-rsync verification succeeded with
no failed artifact, retention skip, or open conflict. Both DBs had the same
strategy source digest and exact research contract.

- White FAST_1M: 1,373/1,374 expected slots, 99.93% coverage, p50 59.86s,
  p95 68.69s, 19 episode keys.
- Grey CONTROL_5M: 275/275 expected slots, 100% coverage, p50 300.04s,
  p95 301.09s, 11 episode keys.
- All 11 Grey episode keys were present in White; White observed eight
  additional keys. Paired entry-time delta p95 was 1,108.52s.
- White exact `$5` book coverage was 97.31%; Grey was 97.13%.
- Both had cursor completion 100%, identity drift 0, and at most two Gamma
  pages per sweep.
- White DB had 48 accepted unique events: EPL 6, Ligue 1 12, LaLiga 18,
  MLS 12, Bundesliga 0.

White hold-to-resolution results remained tiny: threshold 0.98 had 3 resolved
wins and threshold 0.99 had 1 resolved win. The sole 0.99 episode entered at
exact `$5` VWAP 0.996. Thresholds 0.95/0.96/0.97 had five resolved events with
one EPL loss and negative fee-net event-equal ROI. Grey missed that EPL loss
and also had no 0.99 episode. These observations justify treating 1 minute as
the more complete tested cadence, but they do not establish an optimal entry
threshold, stop, cadence, or positive expected value.

## Frozen treatment

- Cat: exact `$5` ask VWAP `[0.98,0.999]`.
- Dog: exact `$5` ask VWAP `[0.99,0.999]`.
- `0.999` is a common upper bound that excludes terminal `1.000`; it is not a
  third arm or a claim that entering at 0.999 is profitable.
- Both: `* * * * *` Jenkins cadence.
- Both: EPL, Bundesliga, Ligue 1, LaLiga, MLS only.
- Both: top-level whole-match home/draw/away moneyline proposition, YES token only.
- Both: explicitly in-play and not ended, game age `[0h,4h]`.
- Both: first arm observation per condition; at most one open position per event.
- Both: full-depth FOK BUY; exact order/fill/fee reconciliation before HOLDING.
- Both: best-bid trigger `<=0.70`, full-holding bid walk, marketable FOK SELL.
- Both: no TP, trailing, time exit, wallet-wide adoption, or account-wide wind-down.
- Exposure: total/event/new-per-cycle `20/1/20`, reentry cooldown 720h.
- Gamma liquidity/volume gates: zero; exact executable CLOB depth is authoritative.

The only A/B treatment difference is the lower entry bound. Account identity is
not randomized, so analysis also reports each job separately. A one-minute poll
does not guarantee a 0.70 stop fill; executable bid depth and gap remain evidence.

## Review gates

- 24h: health only; no profitability or parameter selection.
- 7d: unique-event, crossing, order and exact confirmed-fill coverage.
- Follow-up: fee-net event-clustered return, loss tail, and stop shortfall.
- Any CRITICAL/HIGH fill, fee, cohort, cursor, DB-integrity or manual-position
  contamination gap blocks profitability and scale-up claims.
- The current 0.98/0.99 thresholds are conservative pilot arms, not optimal values.
- No post-v2a threshold, clock, league, stop, exposure, or cadence amendment belongs
  to this cohort. A required change creates another preregistered runtime epoch.
