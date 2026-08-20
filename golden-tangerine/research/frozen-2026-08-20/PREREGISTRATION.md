# Golden Tangerine frozen live A/B protocol — 2026-08-20

- Companion evidence: Golden Black `sports-resolution-paired-v1`
- Entry window: `[2026-08-21T00:00:00Z, 2026-09-20T00:00:00Z)`
- Follow-up cutoff: `2026-10-20T00:00:00Z`
- Cadence: 5 minutes, non-concurrent
- Arm A: `polybot-orange`, exact `$5` ask VWAP `[0.94,0.95]`
- Arm B: `polybot-fox`, exact `$5` ask VWAP `[0.92,0.93]`
- Population: sports, Gamma endDate `(0h,6h]`, liquidity `>=10,000`, cumulative volume
  `>=5,000`, strict standard binary, both outcomes, accepting CLOB only
- Pagination: `/events/keyset`, limit 500, max 4 pages, terminal cursor required
- Signal unit: token's first exact displayed `$5` VWAP observation in its job arm
- Order: fresh full-depth recheck, exact `$5` shares, venue-tick FOK BUY
- Exposure: account 3, event 1, new/cycle 1; no account-wide wallet adoption
- Exit: hold to proven resolution; no TP, stop, trailing, time exit or pre-resolution SELL
- Evidence: run/config cohort, entry episode/snapshot, order intent/status/fill/fee, final payout
- Manual wallet positions are out of scope and must remain untouched
- 24h/7d checks are health only; no threshold tuning before entry/follow-up completion
- This is an explicitly approved low-notional live pilot, not final scale-up evidence
