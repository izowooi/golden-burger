# Golden Watermelon Live dynamic-fee evidence epoch v2c — 2026-08-25

## Provenance and amendment boundary

- Frozen decision timestamp: `2026-08-25T11:50:00Z`.
- Predecessor live epoch: `watermelon-live-cat-98-1m-v2b` /
  `watermelon-live-dog-99-1m-v2b`.
- New live jobs after rollout: `polybot-cat/watermelon-live-cat-98-1m-v2c`
  and `polybot-dog/watermelon-live-dog-99-1m-v2c`.
- Entry clock remains `[2026-08-24T13:00:00Z, 2026-08-31T13:00:00Z)`.
- Follow-up cutoff remains `2026-09-07T13:00:00Z`.
- Cadence remains one minute for both arms, non-concurrent per Jenkins job.
- Order notional remains exact `$5` pre-fee per accepted entry.
- Cohort remains `config_hash × strategy_source_digest × mode × job_name`.

Both v2b Jenkins timers were disabled before this source amendment. Their last
automatic builds started at about `2026-08-25T11:30:37Z`; the v2b DBs remain
immutable operational evidence and are never merged into or rewritten as v2c.

## Defect evidence

The v2b Cat arm bought the Osasuna–Levante draw token as a confirmed taker fill:
exact requested maker notional `$5`, confirmed `5.102` shares at `0.98`, followed
by a proven winning resolution. The CLOB V2 trade payload retained the legacy
`fee_rate_bps=0` placeholder. The ledger treated that field as proof of a zero
fee and recorded settlement assumption `+$0.10204`.

For the same token, Gamma and CLOB market-info both reported the sports dynamic
fee schedule `rate=0.05`, `exponent=1`, `takerOnly=true`. Applying the protocol
formula to the exact fill gives a five-decimal fee of `$0.00500`, so the corrected
fee-net settlement assumption is about `+$0.09704`. This is an evidence defect,
not a loss, threshold failure, partial fill, or position-cap failure. The v2b row
is not mutated or silently backfilled.

## Frozen treatment (unchanged)

- Cat: exact `$5` ask VWAP `[0.98,0.999]`.
- Dog: exact `$5` ask VWAP `[0.99,0.999]`.
- Both: EPL, Bundesliga, Ligue 1, LaLiga, and MLS only.
- Both: top-level whole-match HOME/DRAW/AWAY moneyline proposition, YES token.
- Both: explicit in-play and not ended, game age `[0h,4h]`.
- Both: first arm observation per condition and at most one open position per event.
- Both: full-depth FOK BUY; best-bid `<=0.70` full-holding FOK stop SELL.
- Both: no take-profit, trailing exit, time exit, wallet-wide adoption, or
  account-wide wind-down.
- Exposure: total/event/new-per-cycle `20/1/20`, reentry cooldown 720 hours.
- Gamma liquidity and volume gates: zero; exact executable `$5` CLOB depth is
  the final liquidity gate.

The only A/B treatment difference remains the lower entry bound. This amendment
does not select a threshold winner, tune a parameter, add a league, change the
stop, increase exposure, or change cadence.

## v2c dynamic-fee evidence amendment

1. `market_catalog` stores Gamma `feesEnabled` plus fee rate, exponent, and
   taker-only identity for each exact condition/token mapping.
2. Before any irreversible live FOK order, the bot resolves the token to exactly
   one persisted Gamma condition and requires its complete fee schedule to match
   current CLOB `getClobMarketInfo()` condition, token membership, rate, exponent,
   and taker-only values. Missing, duplicate, malformed, or drifting metadata
   fails closed before order submission.
3. Reconciliation derives maker/taker role from the exact authenticated trade
   correlation, reads the exact persisted requested quantity, normalizes the
   fill quantity representation, and computes the platform fee from exact fill
   size and price. Venue precision is five decimal USDC.
4. The computed fee amount is persisted explicitly in `order_fills`; a legacy
   taker `fee_rate_bps=0` without an explicit fee amount is no longer accepted as
   zero-fee proof. A maker role remains explicit zero-fee evidence.
5. Any identity, quantity, role, schedule, or reported-fee conflict leaves the
   execution outcome in reconciliation rather than inventing fee-net P&L.

No v2c source path may mutate v2b evidence, infer an unknown fee as zero, adopt a
manual wallet position, synthesize a fill or redemption, or bypass the existing
pending-state, capacity, and reconciliation guards.

## Review gates

- Manual deployment and first two natural runs: new runtime DB, source/config/
  preregistration cohort, no clean, non-concurrency, cursor completion, Gamma fee
  metadata coverage, CLOB identity readiness, order health, and DB integrity.
- First 24 hours of v2c: cadence, cursor and league supply, exact-book/candidate
  coverage, capacity/guards, fee schedule coverage, order/fill lifecycle, DB,
  bot logs, and Jenkins logs.
- Seven-day entry end and follow-up retain the v2b review contract, but every
  epoch is reported separately. No threshold winner or profitability claim is
  made from the interrupted v2b health window or an early v2c checkpoint.
