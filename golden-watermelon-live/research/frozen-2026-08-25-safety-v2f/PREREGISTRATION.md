# Golden Watermelon Live exception/stop/deadline safety epoch v2f — 2026-08-25

## Provenance and amendment boundary

- Frozen decision timestamp: `2026-08-25T13:51:06Z`.
- Predecessor live epoch: `watermelon-live-cat-98-1m-v2e` /
  `watermelon-live-dog-99-1m-v2e`.
- New live jobs after rollout: `polybot-cat/watermelon-live-cat-98-1m-v2f`
  and `polybot-dog/watermelon-live-dog-99-1m-v2f`.
- Entry clock remains `[2026-08-24T13:00:00Z, 2026-08-31T13:00:00Z)`.
- Follow-up cutoff remains `2026-09-07T13:00:00Z`.
- Cadence remains one minute for both arms, non-concurrent per Jenkins job.
- Order notional remains exact `$5` pre-fee per accepted entry.
- Cohort remains `config_hash × strategy_source_digest × mode × job_name`.

Both v2e Jenkins timers were disabled before this source amendment. The v2a
through v2e databases remain immutable operational evidence. They are not
rewritten, migrated into, or analytically merged with v2f.

## Frozen treatment (unchanged)

- Cat: exact `$5` ask VWAP `[0.98,0.999]`.
- Dog: exact `$5` ask VWAP `[0.99,0.999]`.
- Both: EPL, Bundesliga, Ligue 1, LaLiga, and MLS only.
- Both: top-level whole-match HOME/DRAW/AWAY moneyline proposition, YES token.
- Both: explicit in-play and not ended, game age `[0h,4h]`.
- Both: first arm observation per token and at most one open position per event.
- Both: full-depth FOK BUY; best-bid `<=0.70` full-holding FOK stop SELL except
  for an explicitly recorded sub-`0.01`-share SDK remainder.
- Both: no take-profit, trailing exit, time exit, wallet-wide adoption, or
  account-wide wind-down.
- Exposure: total/event/new-per-cycle `20/1/20`, reentry cooldown 720 hours.
- Gamma liquidity and volume gates: zero; exact executable `$5` CLOB depth is
  the final liquidity gate.

The only A/B treatment difference remains the lower entry bound. This amendment
does not select a threshold winner, tune a parameter, add a league, change the
stop, increase exposure, or change cadence.

## Inherited lifecycle-safety contract

v2f retains the complete v2d/v2e safety contract:

1. Capacity includes `PENDING_BUY`, `HOLDING`, `PENDING_SELL`, `QUARANTINED`,
   and every potentially live unlinked BUY submission, including reconciled
   positive orphan fills. The same reservations apply to the event cap.
2. An explicit synchronous rejection releases capacity only when `FAILED`, no
   order ID, `success=0`, and no reconciliation requirement are all persisted.
   Unknown outcomes remain reserved and require exact evidence or explicit
   operator proof of no order.
3. Every claimed episode records execution state, reason, and attempt time;
   guard-blocked first observations are not silently retried.
4. Open-order absence never resolves an uncertain POST. Accepted, ambiguous,
   and evidence-write-failed requests stay fail closed.
5. Exact signed maker/taker amounts and actual signed shares are retained.
   Completed BUY, SELL, and resolution lifecycle transitions require complete
   fill/VWAP/fee evidence.
6. BUY and SELL both require exact Gamma↔CLOB condition, token, and dynamic-fee
   agreement. Incomplete open BUY evidence blocks scaling.
7. Resolution requires an exact closed one-hot `0/1` payout with exact
   condition/token/outcome identity; `0.5/0.5` is never terminal evidence.
8. Universe health records raw/qualified/drift counts and candidate rejection
   reasons. Every potentially live orphan remains visible in the denominator.

## v2f independent-review amendment

An independent immutable-commit review found four additional failure paths.
No path had yet produced an order or corrupted a v2e DB, but each could recreate
an earlier fleet symptom after a future fill or source degradation.

### 1. Exception-atomic Trade/episode linkage

`create_trade` previously flushed the new Trade before checking whether the
episode was already linked. If validation then raised, the caller's subsequent
failure annotation could commit the still-pending ghost Trade. v2f validates
first and wraps normal and recovered-orphan creation in explicit try/commit and
exception **Session rollback** boundaries. A failed link cannot survive in the
Session and consume capacity later.

### 2. SDK SELL dust before the depth walk

The current SDK signs SELL shares at two decimal places. v2e walked depth for
the finer BUY fill and only learned the signed quantity afterward, so 5.102
shares with exactly 5.10 shares of bid depth could be rejected before a valid
stop order was signed. v2f computes the two-decimal floor first, walks and signs
that identical quantity, and records the `<0.01` share remainder as explicit
**SDK SELL dust**. A residual at or above one SDK quantum still fails closed.

### 3. Exact orphan catalog identity

Orphan adoption now requires the current sweep's catalog condition and event,
exact aligned `[Yes,No]` token list, selected YES outcome, entry snapshot ID /
condition / token / outcome, strategy name, arm bounds, exact signed `$5` maker
amount, and ledger requested size to agree. Missing or drifting **catalog
identity** keeps the positive fill reserved for operator review instead of
creating a `HOLDING` Trade.

### 4. One-minute source envelope

Gamma keyset requests use connect/read timeouts of `2s/5s`, at most four pages,
and one request attempt per page. A 429, transient 403, timeout, or 5xx fails the
current build immediately; the next one-minute Jenkins build is the retry. The
collector never sleeps for a **60-second Retry-After** inside one cycle. The
four-page socket-timeout envelope is at most 28 seconds before local parsing and
CLOB work, preventing Gamma alone from queuing non-concurrent builds.

No v2f path may mutate predecessor evidence, infer a missing fee or fill, adopt
a manual wallet position, synthesize a redemption, relax the frozen market
identity, or bypass a pending/uncertain execution state.

## Review gates

- Manual deployment and first two natural runs: new runtime DB, exact source /
  config / preregistration cohort, no clean, non-concurrency, cursor completion,
  capacity denominator, terminal-rejection release, exception rollback, orphan
  identity, signable stop quantity, source timeout envelope, and DB integrity.
- First 24 hours of v2f: cadence and overlap, page/cursor and league supply,
  exact-book/candidate funnel, guard reasons, rejected/orphan reservations and
  recovery, signed order quantities, fee/fill lifecycle, stop dust, resolution
  identity, DB growth/integrity, bot logs, and Jenkins logs.
- If no permitted-league candidate/fill occurs, report that evidence gap rather
  than treating zero orders as either success or failure of the thresholds.
- Seven-day entry end and follow-up retain the original prospective contract.
  No 0.98/0.99 winner or profitability claim is made from predecessor epochs or
  an early v2f health checkpoint.
