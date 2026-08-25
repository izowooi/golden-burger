# Golden Watermelon Live terminal-rejection safety epoch v2e — 2026-08-25

## Provenance and amendment boundary

- Frozen decision timestamp: `2026-08-25T13:34:13Z`.
- Predecessor live epoch: `watermelon-live-cat-98-1m-v2d` /
  `watermelon-live-dog-99-1m-v2d`.
- New live jobs after rollout: `polybot-cat/watermelon-live-cat-98-1m-v2e`
  and `polybot-dog/watermelon-live-dog-99-1m-v2e`.
- Entry clock remains `[2026-08-24T13:00:00Z, 2026-08-31T13:00:00Z)`.
- Follow-up cutoff remains `2026-09-07T13:00:00Z`.
- Cadence remains one minute for both arms, non-concurrent per Jenkins job.
- Order notional remains exact `$5` pre-fee per accepted entry.
- Cohort remains `config_hash × strategy_source_digest × mode × job_name`.

Both v2d Jenkins timers were disabled before this source amendment. The v2a,
v2b, v2c, and v2d databases remain immutable operational evidence. They are not
rewritten, migrated into, or analytically merged with v2e.

## Frozen treatment (unchanged)

- Cat: exact `$5` ask VWAP `[0.98,0.999]`.
- Dog: exact `$5` ask VWAP `[0.99,0.999]`.
- Both: EPL, Bundesliga, Ligue 1, LaLiga, and MLS only.
- Both: top-level whole-match HOME/DRAW/AWAY moneyline proposition, YES token.
- Both: explicit in-play and not ended, game age `[0h,4h]`.
- Both: first arm observation per token and at most one open position per event.
- Both: full-depth FOK BUY; best-bid `<=0.70` full-holding FOK stop SELL.
- Both: no take-profit, trailing exit, time exit, wallet-wide adoption, or
  account-wide wind-down.
- Exposure: total/event/new-per-cycle `20/1/20`, reentry cooldown 720 hours.
- Gamma liquidity and volume gates: zero; exact executable `$5` CLOB depth is
  the final liquidity gate.

The only A/B treatment difference remains the lower entry bound. This amendment
does not select a threshold winner, tune a parameter, add a league, change the
stop, increase exposure, or change cadence.

## Inherited lifecycle-safety contract

v2e retains the complete v2d safety contract:

1. Capacity includes `PENDING_BUY`, `HOLDING`, `PENDING_SELL`, `QUARANTINED`,
   and every potentially live unlinked BUY submission, including reconciled
   positive orphan fills. The same reservations apply to the event cap.
2. Ledger-proven orphan BUY recovery requires one unambiguous submission and
   exact order, condition, token, episode, snapshot, arm, terminal positive
   fill, and complete fee agreement. Trade creation and episode linkage are
   atomic for both normal and recovery paths.
3. Every claimed episode records execution state, reason, and attempt time;
   guard-blocked first observations are not silently retried.
4. Open-order absence never resolves an uncertain POST. Unknown outcomes need
   exact venue evidence or explicit operator proof of no order.
5. Exact signed maker/taker amounts and actual signed shares are retained. SELL dust
   below one SDK `0.01`-share quantum is recorded and excluded from sold-portion
   P&L; larger drift fails closed.
6. BUY and SELL both require exact Gamma↔CLOB condition, token, and dynamic-fee
   agreement. Incomplete open BUY fill/VWAP/fee evidence blocks scaling.
7. Resolution requires an exact closed one-hot `0/1` payout with exact
   condition/token/outcome identity; `0.5/0.5` is never terminal evidence.
8. Universe health records raw/qualified/drift counts and candidate rejection
   reasons. Gamma reads use a 10-second timeout and two bounded attempts.

## v2e terminal-rejection amendment

A v2d static audit found one over-conservative capacity path: an explicit,
synchronous venue rejection was stored as a live BUY submission but could remain
reserved forever because it had no linked Trade. Repeated ordinary FOK
rejections could therefore consume all 20 capacity slots despite creating no
orders or positions.

v2e releases an unlinked BUY from capacity only when all of the following are
persisted together:

1. `order_id IS NULL`;
2. `success = 0`;
3. `needs_reconciliation = 0`; and
4. normalized `response_status = 'FAILED'`.

This is terminal no-exposure evidence produced by either an explicit rejected
POST response or an unambiguous non-retryable submission exception. It does not
include `INTENT`, `SUBMIT_OUTCOME_UNKNOWN`, `EVIDENCE_WRITE_FAILED`, malformed
responses, timeouts, connection failures, or 5xx errors. Those rows remain
quarantined and reserve capacity until exact evidence or operator proof resolves
them. Accepted orders, positive orphan fills, and terminal orders with nonzero
matched size also remain reserved until atomically linked to a Trade.

No v2e path may mutate predecessor evidence, infer a missing fee or fill, adopt
a manual wallet position, synthesize a redemption, relax the frozen market
identity, or bypass a pending/uncertain execution state.

## Review gates

- Manual deployment and first two natural runs: new runtime DB, exact source /
  config / preregistration cohort, no clean, non-concurrency, cursor completion,
  capacity denominator, explicit-rejection release, unresolved-outcome reserve,
  episode execution outcomes, order health, and DB integrity.
- First 24 hours of v2e: cadence and overlap, page/cursor and league supply,
  exact-book/candidate funnel, guard reasons, rejected/orphan reservations and
  recovery, signed order quantities, fee/fill lifecycle, resolution identity,
  DB growth/integrity, bot logs, and Jenkins logs.
- If no permitted-league candidate/fill occurs, report that evidence gap rather
  than treating zero orders as either success or failure of the thresholds.
- Seven-day entry end and follow-up retain the original prospective contract.
  No 0.98/0.99 winner or profitability claim is made from predecessor epochs or
  an early v2e health checkpoint.
