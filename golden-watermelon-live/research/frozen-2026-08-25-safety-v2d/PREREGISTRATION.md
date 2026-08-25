# Golden Watermelon Live lifecycle-safety epoch v2d — 2026-08-25

## Provenance and amendment boundary

- Frozen decision timestamp: `2026-08-25T12:58:00Z`.
- Predecessor live epoch: `watermelon-live-cat-98-1m-v2c` /
  `watermelon-live-dog-99-1m-v2c`.
- New live jobs after rollout: `polybot-cat/watermelon-live-cat-98-1m-v2d`
  and `polybot-dog/watermelon-live-dog-99-1m-v2d`.
- Entry clock remains `[2026-08-24T13:00:00Z, 2026-08-31T13:00:00Z)`.
- Follow-up cutoff remains `2026-09-07T13:00:00Z`.
- Cadence remains one minute for both arms, non-concurrent per Jenkins job.
- Order notional remains exact `$5` pre-fee per accepted entry.
- Cohort remains `config_hash × strategy_source_digest × mode × job_name`.

Both v2c Jenkins timers were disabled before this source amendment. The v2a,
v2b, and v2c databases remain immutable operational evidence. They are not
rewritten, migrated into, or analytically merged with v2d.

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

## Defect class addressed

The first v2b/v2c operational evidence did not exhibit a max-position lock,
partial-fill lock, overlapping cycle, cursor failure, or unresolved position.
A cross-check against earlier fleet incidents nevertheless found latent paths
that could recreate the same symptoms after a future order:

1. A confirmed BUY ledger row could survive a process/DB boundary without a
   linked Trade and then disappear from the position-cap denominator.
2. `QUARANTINED` exposure and event-local orphan BUY exposure were not included
   in every capacity check.
3. An uncertain POST could be classified as no order merely because it was
   absent from the open-order list, even though a filled FOK is also absent.
4. The signed SELL quantity can be truncated by the SDK to two decimal shares;
   comparing it with the finer BUY quantity could leave `PENDING_SELL` stuck.
5. Ambiguous `0.5/0.5`, drifting condition/token identity, or incomplete BUY fee
   evidence could release a position as resolved.
6. SELL had no symmetric Gamma/CLOB dynamic-fee preflight, and an open position
   with incomplete BUY fill/fee evidence did not independently block scaling.
7. A claimed first-observation episode could remain unexplained when a guard or
   fail-closed execution branch rejected the order.
8. Long Gamma timeout/retry bounds could queue one-minute Jenkins builds during
   an API degradation.

## v2d lifecycle-safety amendment

1. Open capacity includes `PENDING_BUY`, `HOLDING`, `PENDING_SELL`, and
   `QUARANTINED`, plus every live BUY submission not linked to a Trade. A
   reconciled positive orphan remains reserved; only an exact linked Trade,
   explicit operator proof of no order, or terminal exact zero-fill releases it.
   The same reservation is included in the event cap.
2. A single ledger-proven orphan BUY may be reconstructed only when its exact
   order, condition, token, first-observation episode, snapshot, config arm,
   terminal positive fill, and complete fee evidence all agree. The Trade and
   episode link are committed atomically. Ambiguous or duplicate evidence stays
   reserved and blocks all new BUYs.
3. Normal Trade creation and first-observation linkage are also one transaction.
   Every claimed episode records `execution_state`, `execution_reason`, and last
   attempt time. Guard-blocked episodes remain the frozen first observation and
   are not silently retried later.
4. Uncertain POST outcomes are never auto-released from open-order absence.
   They require exact venue/order/fill evidence or an explicit operator-backed
   no-order resolution.
5. The execution ledger persists the signed maker/taker amounts and the actual
   signed share request. A SELL residual below one SDK `0.01`-share quantum is
   recorded explicitly and excluded from sold-portion realized P&L; a residual
   at or above that quantum fails closed. Requested and confirmed SELL size are
   compared on the signed quantity, not the finer pre-signing quantity.
6. Both BUY and SELL require exact Gamma↔CLOB condition, token membership, and
   dynamic-fee schedule agreement before an irreversible POST. An owned open
   position missing confirmed BUY size, VWAP, or explicit fee amount blocks new
   entries. Resolution and completed SELL also require complete fee evidence.
7. Gamma resolution requires an exact closed one-hot `0/1` payout and exact
   condition/token/outcome alignment. `0.5/0.5` is never terminal evidence.
8. Universe audit exposes raw/qualified/drift counts and preserves every first
   candidate rejection reason. Gamma keyset read timeout is 10 seconds with two
   bounded attempts so a degraded source fails within the one-minute envelope
   instead of creating a long non-concurrent queue.

No v2d path may mutate predecessor evidence, infer a missing fee or fill,
adopt a manual wallet position, synthesize a redemption, relax the frozen
market identity, or bypass a pending/uncertain execution state.

## Review gates

- Manual deployment and first two natural runs: new runtime DB, exact source /
  config / preregistration cohort, no clean, non-concurrency, cursor completion,
  capacity denominator, episode execution outcomes, order health, and DB
  integrity.
- First 24 hours of v2d: cadence and overlap, page/cursor and league supply,
  exact-book/candidate funnel, guard reasons, orphan reservations/recovery,
  signed order quantities, fee schedule and fill lifecycle, resolution identity,
  DB growth/integrity, bot logs, and Jenkins logs.
- If no permitted-league candidate/fill occurs, report that evidence gap rather
  than treating zero orders as either success or failure of the thresholds.
- Seven-day entry end and follow-up retain the original prospective contract.
  No 0.98/0.99 winner or profitability claim is made from v2b, early v2c, or an
  early v2d health checkpoint.
