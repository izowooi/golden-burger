# Golden Cherry Shadow Resolution v2 — Prospective Preregistration

## Identity and operating boundary

- Runtime job: `cherry-shadow-resolution-v2`
- Data contract: `cherry-shadow-resolution-v2`
- Preregistration ID: `cherry-shadow-resolution-v2-prereg-2026-09-05`
- Entry window: `[2026-09-04T16:00:00Z, 2026-10-04T16:00:00Z)`
- Follow-up window: `[2026-10-04T16:00:00Z, 2026-11-03T16:00:00Z)`
- Cadence target: five minutes; each invocation has a 240-second cooperative budget.
- Mode: accountless, credential-free, order-free, shadow-only.
- Database: `data/cherry-shadow-resolution-v2/trades_sim.db`. It must never be merged
  into or substituted for legacy `data/default/trades.db`.

The runtime stores `config_hash × strategy_source_digest × preregistration_sha256 ×
job_name × mode` on every run. Git commit is provenance only. Any change to the frozen
treatment cells requires a new data contract, runtime job, database epoch, and
preregistration rather than an in-place edit.

## Bootstrap evidence and limitation

The design was selected from actual confirmed Golden Cherry BUYs since
`2026-08-14T00:00:00Z`, resolved through Gamma final outcome prices aligned to the exact
CLOB token:

| Slice | n | Hold-to-resolution ROI |
|---|---:|---:|
| Actual entry 0.75–0.88 | 1,099 | -1.20% |
| Pregame | — | +4.41% |
| In-play | — | -2.25% |
| Actual entry 0.80–0.82 | 190 | +6.75% |
| 0.80–0.82 first time half | — | +9.75% |
| 0.80–0.82 second time half | — | +4.38% |

These are filtered actual-entry observations, not randomized or causal evidence. The
0.80–0.82 result may reflect selection, timing, category, event clustering, execution,
or temporal confounding. It is therefore a candidate treatment cell, not a promoted
live parameter.

## Frozen universe and identity contract

Each entry sweep uses Gamma `/markets/keyset` with server-side `closed=false`,
`liquidity_num_min=125000`, `volume_num_min=5000`, `include_tag=true`, and terminal
cursor traversal. Every returned row is revalidated client-side for active, not closed,
order-book enabled, accepting orders, liquidity, cumulative volume, exact condition ID,
and aligned outcome/price/token arrays.

The primary outcome is array index 0, matching Yellow YES-only behavior. The label is
stored verbatim and is never assumed to literally equal `Yes`. Entry discovery uses
Gamma index-0 probability `[0.75,0.88]`; treatment assignment uses the exact full-depth
displayed ask VWAP for a `$5` hypothetical entry.

Time strata are frozen as follows:

- `PRE_GAME`: sports evidence exists, `gameStartTime` is valid, and kickoff is in
  `(0h,120h]`.
- `IN_PLAY`: sports evidence exists, kickoff has passed, and Gamma still reports the
  market as open and accepting orders.
- `NON_SPORTS`: no sports timing evidence and `endDate` is in `(0h,120h]`.
- Sports with missing/invalid `gameStartTime` fail closed rather than borrowing
  `endDate`.

Event ID is the primary clustering key. If missing, the market is excluded rather than
assigned to a guessed event. Category, tags, event title/slug, sports type, kickoff,
end date, and source receipt time are retained.

## Frozen paired entry cells

Every eligible selected condition receives all three decisions from the same source
book and subsequently shares the same condition/token path when a band opens:

| Cell | Exact `$5` ask VWAP | Role |
|---|---:|---|
| `control_low_076_078` | `[0.76,0.78]` | control |
| `primary_080_082` | `[0.80,0.82]` | primary candidate |
| `control_high_084_086` | `[0.84,0.86]` | control |

Bounds are inclusive. A condition/token can open each band at most once, on its first
observed exact-book match. Book-cap exclusions follow deterministic
`event_id × condition_id × token_id` ordering and remain explicit evidence, not
negative outcomes.

## Frozen exit policies

Each opened episode instantiates every policy below. Trigger and execution use the
same full displayed bid book for the original hypothetical shares. Insufficient depth
is censored and never filled at a guessed price.

- Primary: `hold_to_resolution`.
- Current control: `current_tp10_sl08_trail05`.
- One-factor sensitivities: TP 5%, TP 15%, SL -5%, SL -12%, and no trailing, holding
  every other current-policy component fixed.

The current-style policy checks stop loss, then take profit, then trailing drawdown.
Trailing peak starts at entry VWAP and subsequently uses the maximum exact executable
bid VWAP observed. Resolution requires Gamma `closed=true`, aligned arrays, unique
one-hot final `0/1` prices, and the exact episode token. Resolution settlement is a
displayed-book counterfactual, never an order fill, realized P&L, redeem, or account
cashflow.

No cell or policy may be dropped, renamed, widened, promoted, or declared the winner
during collection. Reports remain event-clustered and show every cell, unresolved
censoring, book coverage, and run/sweep health.

## Success, failure, and review gates

A run may publish `SUCCEEDED` only after terminal cursor completion, deterministic
membership publication, all required selected-token book attempts, path evidence for
all open episodes in scope, and a SQLite integrity probe. Budget exhaustion, repeated
cursor, malformed identity, missing required book response, or incomplete publication
records `FAILED`; partial data is never relabeled as successful.

- First 24 hours: collection health only.
- First 7 days: coverage, cadence, clustering, and resolution follow-up only.
- No treatment or exit-policy comparison before the 30-day entry window closes.
- Final comparison waits through the follow-up window and reports unresolved episodes
  as censored. No live change follows automatically from this experiment.
