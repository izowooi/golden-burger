# Cat/Dog Soccer + MLB account consolidation v4

Implementation/validation contract, not a deployment or profitability claim.
Main paused Cat/Dog/Bear/Tiger timers at approximately 2026-09-06 10:05–10:06 UTC.
First successful new-MLB run is the prospective epoch start; do not backfill the
pause or count earlier Bear/Tiger trades as Cat/Dog observations.

## Frozen ownership and policies

| Account | Soccer (existing DB) | MLB (NEW, empty DB) | BUY band |
|---|---|---|---|
| polybot-cat | watermelon-live-cat-96-1m-v2h | watermelon-live-cat-mlb-96-1m-v4 | .96–.999 |
| polybot-dog | watermelon-live-dog-99-1m-v2h | watermelon-live-dog-mlb-99-1m-v4 | .99–.999 |

DBs remain `data/<runtime>/trades.db`. No wallet trade copying, no Bear/Tiger
credentials, history or manual-position adoption. Soccer DBs and all numeric
parameters/periods remain intact. Existing six runtime guards remain valid,
including close_only and retired NHL configurations. Bear/Tiger retirement is
a separate operator workflow: stop new entries, retain own-wallet management,
verify pending/unknown/exposure/redeem/dust before disabling. This code does not
retire, liquidate, move funds or stop their jobs.

Immutable `SPORT_POLICIES` explicitly owns entry/stop/risk settings. Soccer is
4h, MLB 8h; stop .70, entry-relative drawdown .30, stop slippage .05, spread .10,
loss fraction .35, per-runtime emergency SELL limit 1, experiment capital $100,
drawdown .10, event limit 1 remain unchanged. Current target is $5. New Cat/Dog
MLB has a distinct `catdog_mlb` profile; future changes require a new frozen review
and do not silently retune Bear/Tiger or NHL. Entry dates remain the approved
8/29 start, 9/12 09:05Z entry end and 9/19 09:05Z follow-up reference. Each sport's
economic P&L/epoch guard remains independent; no loss history reset/merge.

## Account safety and orchestration

- One nonblocking account lock spans the entire child cycle, including all
  reconciliation/SELL and BUY work. Direct registered single `run`/`status`
  commands use it too; registered Bot construction/run without it fails.
- A separate orchestrator lock serializes `run-account`. Children are sequential
  processes with independent environment copies and SDK pools. First family
  rotates by UTC minute. Parent does not hold the child account lock.
- Combined capacity is **20**, not 20+20: both repositories' existing open-state
  and untracked BUY reservation predicates are summed. Unknown, malformed,
  foreign or missing peer DB is not zero: it blocks BUY, not healthy member
  management. Read peer databases only with SQLite mode=ro/query_only.
- Combined BUY attempt budget is **5 in rolling 60 seconds**, using existing
  `order_submissions.submitted_at`, including failed attempts conservatively.
  No new claims, money state/control DB or unbacked reservation subsystem exists.
  A long parent run can cross rolling windows; this is not a hard 5-per-build
  cap independent of time. Existing per-runtime cycle limit 5 is also retained.
- Before every BUY, reread both ledgers and authenticated collateral balance
  (SDK 1.0.2 `get_balance_allowance`, integer micro-USDC / 1e6). Deduplicate pending
  cash by submission ID and subtract unresolved BUY principal. Exact no-order
  proofs release reservations using existing ledger/repository rules.
- Cash failure disables remaining BUYs in that child; successful run statistics
  preserve the flag in its existing DB for sibling checks over the next 60s.
  Recent ledger balance/allowance errors also block BUY. If a child process
  fails, the parent runs the other child with BUY disabled, not management off.
  New balance reads can recover after the failed interval; no balance or fee is
  fabricated. This is principal admission, not proof of free allowance/fees or
  protection against an external manual wallet writer. Existing FOK/fee checks
  remain mandatory and wallet rejections remain possible.
- Missing native DB files are never auto-created by `run`. Explicit one-time
  `prepare-account` requires the existing valid soccer DB and an absent MLB
  target, creates only the new MLB schema/ledger, and refuses adoption/reset of
  an existing target. Incomplete preparation requires operator inspection.
- No hard deadline/alarm/subprocess kill is added. Finite HTTP timeouts and
  overlap locks protect execution; <60s must be measured by Main after deployment.

## Operator interface (no operations performed by this change)

```bash
# Keep existing credentials, lifecycle and common exports. Run from this project.
polybot prepare-account --live --account polybot-cat
polybot run-account --live --account polybot-cat
# Same commands with polybot-dog for Dog.
```

Parent shell can retain soccer hours=4. `profile_environment` sets each child's
frozen family, arm, hours and stop fields without mutating the parent env. Never
run both new runtimes with one unmodified soccer environment. Parent reports
any child failure after attempting both profiles. Source/config cohorts change;
same soccer DB does not mean the new execution epoch is statistically identical.
The original soccer preregistration hash is retained; new MLB uses this contract,
and the strategy source digest includes this supplement for all new-source runs.

Required tests: original six config guards and values; no profile env leak;
explicit fresh-DB preparation with original byte preservation; missing/foreign
peer fail-closed; aggregate slots including orphan/pending; rolling BUY5 across
profiles/restarts/failed attempts; pending cash and true SDK fixed-6 decoding;
account lock exclusion and parent/child nondeadlock; one profile failure leaves
other management running; no timeout kill or manual-position import. Full
project regression tests are required before Main's manual deployment checks.

Pre-deployment review additions: persisted unknown BUY reservations must not
also consume a local fallback slot. Canceled orphan orders with NULL matched
quantity remain reserved; only explicit zero matched quantity plus the existing
terminal/no-positive-fill evidence releases them. Capacity, attempt quota,
cash and evidence failures have distinct reason codes and retain their known
aggregate counters. Tests cover 19 slots admitting, 20 slots blocking, an
18-position + one persisted unknown scenario, and NULL-to-proven-zero transitions.
