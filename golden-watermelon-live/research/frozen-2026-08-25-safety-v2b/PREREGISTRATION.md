# Golden Watermelon Live 1-minute safety epoch v2b — 2026-08-25

## Provenance and amendment boundary

- Frozen decision timestamp: `2026-08-24T16:10:00Z`.
- Predecessor live epoch: `watermelon-live-cat-98-1m-v2a` /
  `watermelon-live-dog-99-1m-v2a`.
- Active live jobs after rollout: `polybot-cat/watermelon-live-cat-98-1m-v2b`
  and `polybot-dog/watermelon-live-dog-99-1m-v2b`.
- Entry: `[2026-08-24T13:00:00Z, 2026-08-31T13:00:00Z)`.
- Follow-up cutoff: `2026-09-07T13:00:00Z`.
- Cadence: one minute for both arms, non-concurrent per Jenkins job.
- Order notional: exact `$5` per accepted entry.
- Cohort: `config_hash × strategy_source_digest × mode × job_name`.

The v2a cutoffs were independently synchronized and verified before this
amendment. Cat and Dog each had 56 successful, cursor-complete runs with zero
trade, entry episode, order submission, fill, or open state. Every observed
market was excluded as `league_not_allowed`; the old detail checkpoint defect
stored no excluded condition rows, so the exact rejected sport distribution
was not recoverable from the DB. There is no bot-owned position to migrate.
The v2a DBs remain immutable deployment evidence and are not merged with v2b.

This amendment is required because fleet history showed that unresolved order
states, orphan execution intents, and incomplete candidate evidence can make a
bot appear idle or consume capacity without an explainable denominator. It is
a safety and observability amendment, not a treatment or parameter amendment.

## Frozen treatment (unchanged from v2a)

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

The only A/B treatment difference remains the lower entry bound. The common
upper bound `0.999` only excludes terminal `1.000`. Neither threshold is claimed
to be optimal.

## v2b safety and evidence amendment

1. The max-position denominator is `open trade states + unresolved live BUY
   submissions not represented by an open trade`. This prevents an accepted or
   ambiguous orphan BUY from bypassing the 20-position reservation cap, while
   avoiding double-counting a ledger row linked to an open trade.
2. A tracked `PENDING_BUY` or `PENDING_SELL` blocks all new BUY execution until
   the normal reconciliation phase resolves it. Candidate scanning still runs
   and is recorded, so safety blocking is not misread as opportunity scarcity.
3. An unresolved SELL outcome or SELL reconciliation gap blocks new BUYs. An
   unresolved BUY remains token/side-local, reserves one capacity slot, and is
   reported as degraded health; it is not silently discarded or globally
   released.
4. Same-cycle ambiguous BUY outcomes reserve local capacity immediately, even
   if the long-lived SQLAlchemy session cannot yet observe the ledger writer's
   commit.
5. Membership detail checkpoints persist qualified and excluded conditions.
   A prior checkpoint whose row count differs from its declared unique
   condition count forces an immediate repair checkpoint rather than waiting
   for the next daily interval.
6. Classifier exclusions include a bounded normalized source sport code and
   classifier status, so `zero qualified` can be separated into legitimate
   out-of-universe supply versus frozen-league identity drift.
7. Run audit stats expose entry guard reasons, reserved/open capacity,
   candidates observed while blocked, and BUY/SELL-specific unresolved or
   reconciliation-gap counts.

No source amendment may auto-release uncertain BUY evidence, adopt a manual
wallet position, synthesize a fill or fee, lower the entry threshold, increase
the position cap, or change the clock/cadence inside this cohort.

## Review gates

- First two natural runs: source/config/preregistration cohort, no clean,
  non-concurrency, cursor completion, detailed membership cardinality, order
  health, and DB integrity.
- First 24 hours from the first successful v2b build: cadence, cursor and
  five-league supply, qualification/exclusion distributions, exact-book and
  candidate coverage, entry guard/capacity, order/fill/fee reconciliation,
  open-state lifecycle, DB integrity, bot log, and Jenkins log.
- Seven-day entry end: unique event and threshold-crossing coverage, exact FOK
  submission/fill coverage, stop depth/gap, and fee evidence.
- Follow-up: event-clustered fee-net return and loss tail only after every
  CRITICAL/HIGH evidence gap is cleared.

The 24-hour gate does not choose a winning arm, tune thresholds, or claim
profitability. A required treatment, clock, league, stop, exposure, or cadence
change creates another preregistered runtime epoch.
