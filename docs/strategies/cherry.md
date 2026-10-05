# Golden Cherry — porting spec (Resolution Momentum)

> **2026-10-05 현행 설계 (back to basics, 연구자 결정 `cherry:back-to-basics`).** cherry 는 다시 전 카테고리 전략이다.
> 거래량(·유동성) 하한을 넘는 마켓 중 기준 종료 시각(`general.store.end_ref`: 경기 마켓은 킥오프+3h, 나머지는 Gamma
> `endDate`)까지 `hours_to_end_min`–`hours_to_end_max` 시간 남았고, 종료 `min_listed_hours`(72h) 전부터 상장돼 있던
> 마켓에서 **앞선 쪽(≥0.5) 결과**가 [entry_min, entry_max] 이면 마켓당 한 번 FOK taker 로 산다. 청산은 절대 매도가
> 또는 진입가+delta(수수료 후 순이익일 때만 전량), 선택적 0.99 이상 보유, 선택적 종료 X시간 전 시간 청산, 손절·trailing
> 없음. 데이터는 `data/general/`(`polylab general discover|poll|backfill`, core.db 가 추적하는 5개 종목 경기 마켓은
> core 에서 읽는다). 코드 `src/polylab/strategies/cherry.py`, 백테스트 `src/polylab/analysis/cherry_basics.py`,
> 결과·채택값 `docs/research/backtests/2026-10-05-cherry-basics.md`. 아래 본문은 레거시 포팅 명세(역사 기록)이며
> 2026-10-05 이전 포지션의 고정 청산 규칙만 여전히 이를 따른다.

Everything below is per source at `a21db7c` (golden-cherry tracked tree is clean at that commit).
Paths are relative to `golden-cherry/` unless prefixed. Live values come from the 2026-09-13
preregistration and the local-only Jenkins inventory, not from source. Before treating them as
current, re-check them against the runtime DB `strategy_configs`/RunAudit resolved config and the
Jenkins effective env (t1 `AGENTS.md`, "운영 데이터의 권위").

Execution mechanics are in [execution-legacy-notes.md](execution-legacy-notes.md) and are only
referenced here: GTC signing, tick rounding, the execution ledger, exact fill evidence, fees,
`assert_submission_allowed` quarantine, and pending-sell recovery. Cherry is **not** a
sports-moneyline strategy. It has no league classifier, no sports clock and no FOK ladder.

## 1. Hypothesis

- **Bias targeted:** favorite–longshot underpricing of near-certain outcomes (`STRATEGY.md:5`).
- **Trade:** buy the leading outcome at a 75–92% implied probability within 0–120h of its reference
  time, then ride convergence toward 1.0. The position is managed by TP, SL and trailing stop, not
  held to resolution.
- **Historical caveat:** the 2026-07 retro found the realized win rate ≈ entry price in every band,
  so no edge was observed there (`STRATEGY.md:18`). The ex-post TP advantage was retracted on
  2026-07-29 (`STRATEGY.md:20-22`). Treat the edge as unproven.

## 2. Market universe (every category, not sports-specific)

### 2.1 Gamma keyset sweep

`api/gamma_client.py:185-230` sweeps `GET /markets/keyset` with these params:

| Param | Value |
|---|---|
| `closed` | `false` |
| `include_tag` | `true` |
| `limit` | 100 |
| `liquidity_num_min` | `effective_min_liquidity` |
| `volume_num_min` | 5000 |
| `after_cursor` | cursor pagination |

- The cumulative-volume floor is the constant `_CHERRY_MIN_CUMULATIVE_VOLUME = 5_000.0`
  (`strategy/scanner.py:20`). It is Gamma lifetime `volume`, not 24h volume and not book depth
  (`AGENTS.md` item 11).
- The sweep raises on page-cap overflow and on a repeated cursor. It records a sweep attestation with
  a membership digest.

### 2.2 Client-side qualification

`api/gamma_client.py:81-106` fails closed. A market qualifies only if **all** of these hold:

- `active is True`, `closed is False`, `enableOrderBook is True`, `acceptingOrders is True`
- finite `liquidity >= min_liq`
- finite `volume >= 5000`

The output is deduplicated by `conditionId` and keeps Gamma order. **Candidates are never sorted**,
so the first qualifying market in keyset order wins the single per-cycle slot.

### 2.3 Category exclusion

- `is_sports_market` (`strategy/filters.py:95-134`) matches `excluded_categories` against tags,
  question and slug, plus a hard-coded `SPORTS_KEYWORDS` list (`filters.py:8-25`).
- `config.yaml:74` sets `excluded_categories: []`, which disables the filter completely
  (`filters.py:111`). Sports, esports, politics, macro and every other category are all eligible.
- The dataclass default (`config.py:133-136`) is a sports exclusion list. A port must preserve that
  **an explicit YAML `[]` overrides the non-empty default**.

### 2.4 Outcome selection

`get_high_probability_outcome` (`filters.py:182-230`):

- **Default:** take index 0 if `outcomePrices[0] >= outcomePrices[1]`, else index 1. Only indices
  0–1 are looked at.
- **`yes_only_mode`:** always index 0 (`filters.py:206-212`). The live A/B is documented as
  "YES-only" (`../docs/retro/2026-09-13-golden-cherry-live-ab-preregistration.md:9`), and the
  README's Jenkins example sets `POLYBOT_YES_ONLY='true'` (`README.md:325`).
- **Sports-market shapes:** there is no moneyline or draw-specific logic. A 3-way soccer market on
  Polymarket is separate binary Yes/No markets, and each is treated like any other binary. Named
  2-outcome markets (team A/B, Over/Under) take index 0 in YES-only mode.

### 2.5 Sports timing

`evaluate_game_start` (`scanner.py:90-187`):

- **Sports-timed test:** `gameStartTime` or `sportsMarketType` is present.
- **Missing `gameStartTime`:** rejected when `reject_sports_without_game_start` (default true). It
  never falls back to `endDate`.
- **Minutes to start:** `minutes_left = (gameStartTime - now)/60`. `phase = in_play` if
  `minutes_left <= 0`, else `pregame`.
- **In-play eligibility:** in-play is valid only if `allow_in_play`. There is no sports clock, game
  minute or score input. "In-play" means only that kickoff has passed while Gamma still says
  `acceptingOrders`.

### 2.6 Data inputs per cycle

- **Gamma full keyset sweep.** Fields used: `conditionId`, `slug`, `question`, `outcomes`,
  `outcomePrices`, `clobTokenIds`, `liquidity`, `volume`, `active`, `closed`, `enableOrderBook`,
  `acceptingOrders`, `endDate`, `gameStartTime`, `sportsMarketType`, `tags`.
- **CLOB midpoint** (`get_midpoint`, batch `get_midpoints` for holdings via `midpoint_snapshot`,
  `bot.py:176`).
- **CLOB conditional-token balance** before a SELL (`trader.py:470`).
- **Gamma `/markets?condition_ids=` per holding** (resolution guard), retried with `closed=true`
  (`gamma_client.py:325-360`).
- **No order-book depth is read by the live path.** Only the shadow walks full books (§8).

## 3. Entry rules (exact order)

Cycle order is set in `bot.py:139-232`:

- **Phase 0** – reconcile.
- **Phase 1** – manage holdings.
- **Entry guard** – evaluated next.
- **Phases 2/3** – run only if `lifecycle_mode == active` **and** `entry_guard.entry_allowed`.

### Scan (`scanner.py:312-489`)

1. The market is in the qualified universe (§2.1–2.2). Liquidity is checked again at
   `scanner.py:355`.
2. The outcome selected in §2.4 has a `token_id`.
3. **Price** is the Gamma `outcomePrices[idx]`, not the book. It must satisfy
   `buy_threshold <= p <= sell_threshold`, **both ends inclusive** (`filters.py:233-253`).
   `sell_threshold` is the entry *upper bound*, not an exit rule (`AGENTS.md` item 1). `should_sell`
   in `filters.py:256` is dead code.
4. **Timing** (reference = `gameStartTime` if the game-start filter is enabled and the time parsed,
   else `endDate`):
   - **In-play** (valid only with `allow_in_play`): no time window applies (`scanner.py:399-405`).
   - **Otherwise** `is_valid_time_entry` (`scanner.py:209-243`) applies, with
     `hours_left = (ref - now)/3600`:
     - reject if `<= 0` (`already_resolved`), `> entry_hours_max` (`too_early`) or
       `< entry_hours_min` (`too_late`);
     - reject if `exit_hours > 0 and hours_left <= exit_hours`;
     - `exit_hours` is forced to 0 when the reference is `game_start_time` (`scanner.py:411-415`).
   - **Effective window:** `0 < h <= 120` when `entry_hours_min=0`.

### Pre-order checks (`trader.py:180-397`)

Checks run in this order:

1. The token is not operator-protected (`operator_controls.py`; `repository.py:58`).
2. The entry guard allows entry (§6).
3. `buys_placed_this_cycle < max_new_positions_per_cycle` (`trader.py:219`).
4. **One entry per `conditionId`, forever.** `is_already_traded` (`repository.py:71-85`) is true for
   *any* `trades` row (including UNFILLED/COMPLETED) or any `skipped_markets` row. The check is
   repeated in `bot.py:217`. The dedupe scope is per DB, i.e. per `--job`. There is **no
   per-event dedupe**: two markets of the same event can both be bought on different cycles.
5. **Position cap.** `reserved_position_count < max_positions` (`trader.py:252`). Reserved count is
   trades in `PENDING_BUY/HOLDING/PENDING_SELL/QUARANTINED` (`repository.py:21-26`) **plus** untracked
   live BUY submissions (`db/exposure_reservations.py`; `repository.py:321-407`).
6. **Notional cap.** `reserved_open_notional + buy_amount <= max_open_notional_usdc`
   (`trader.py:256`). Notional is summed `buy_amount` plus untracked `requested_price*requested_size`.
7. **Liquidity.** `liquidity >= effective_min_liquidity = max(min_liquidity, buy_amount /
   max_order_liquidity_ratio)` (`config.py:140-146`; `trader.py:268`).
8. **Midpoint re-check.** `m = CLOB midpoint`.
   - If `m > sell_threshold`, write `skipped_markets(reason='rapid_jump')`. This **permanently
     excludes the market** (`trader.py:286-292`).
   - If `m < buy_threshold`, skip for this cycle only.
9. **Size.** `shares = buy_amount / m`. Reject if `< 5` shares (`MIN_ORDER_SIZE`, `trader.py:133,303-306`).
   At `$5` this means `m <= 1.0`, so any live band passes.
10. **Game state re-check.** Pregame→in-play is re-evaluated just before POST, and the timing window
    is re-checked (`trader.py:320-365`).
11. **Order.** GTC limit BUY at `round_to_tick(m, 0.01)`, which rounds to nearest and clamps to
    `[0.01, 0.99]` (`api/clob_client.py:238-257`, `550`). The order is `size=shares`. No FOK, no
    ask walking.
    - Live orders are recorded `PENDING_BUY` with `max_price = m` (`trader.py:386-420`).
    - Simulation orders go straight to `HOLDING`.

**Balance/allowance rejection.** If the BUY is rejected for balance or allowance, no further BUYs are
placed this cycle (`trader.py:437-442`).

### Pending BUY (`trader.py:603-710`)

- **Exact confirmed full fill:** becomes `HOLDING` with these fields replaced by confirmed evidence:
  - `buy_price` = confirmed VWAP
  - `buy_shares` = confirmed size
  - `buy_amount` = `size*vwap + fee`
  - `max_price` is **not** reset and keeps the submit midpoint.
- **Terminal zero fill:** becomes `UNFILLED`.
- **Past `pending_buy_ttl_minutes`:** `cancel_order_for_reconciliation`, then `UNFILLED` only on
  proven zero fill.
- **Partial fills** are not auto-cancelled.

## 4. Exit rules (every cycle, HOLDING only; `trader.py:1047-1308`)

### Resolution guard (`trader.py:1070-1097`)

1. Operator-protected tokens are never sold.
2. The guard runs `get_market_by_condition_id`.
   - **Exact one-hot payout proof** (`filters.py:40-92`):
     - `closed is True`;
     - aligned `outcomes`/`outcomePrices`/`clobTokenIds`;
     - every price is in {0,1} with exactly one 1;
     - unique token match.
   - With that proof the trade becomes `RESOLVED` (`trader.py:864-964`). The settlement
     *assumption* is:
     `assumption = (payout - confirmed_vwap) * remaining_shares - buy_fee * min(1, remaining/confirmed_size)`
     (`trader.py:911-920`). No SELL is recorded and there is no redeem call.
   - If the market is `closed`, `active=False` or `acceptingOrders=False` without proof, **the SELL
     is blocked**. This guards against a dead-book midpoint of 0.50.
   - If the Gamma lookup fails, it does **not** block the risk exits.

### Price

`p = CLOB midpoint`. If the midpoint is unavailable, fall back to the Gamma resolution check and do
not trade (`trader.py:1017-1045`).

- **High-water mark:** `max_price = max(max_price, p)`, persisted.
- **Return:** `pnl = (p - buy_price)/buy_price`, where `buy_price` is the confirmed BUY VWAP.

### Exit chain (if/elif; first hit wins)

1. `pnl <= stop_loss_percent` gives `stop_loss` (`trader.py:1123`).
2. `pnl >= take_profit_percent` gives `take_profit` (`:1132`). There is no clamp at 1.0: with TP=0.10,
   entries ≥0.909 can never TP. With TP=0.20 that point drops to entries ≥0.834, which the
   current bands .76–.82 stay below.
3. If trailing is enabled: `p < max_price*(1 - trailing_percent)` gives `trailing_stop` (`:1142`).
4. Separate `if`: `time_based.enabled and exit_hours>0 and 0 < hoursTo(endDate) <= exit_hours`
   gives `time_exit` (`:1156-1167`). It is disabled when `exit_hours=0`, and a past `endDate` never
   triggers it.

**Geometry.** `max_price` starts at the submit midpoint. When `trailing% < |SL|`, the trailing stop
fires before SL on any position that never rose (`STRATEGY.md:46`). With the live trailing at 15% and
SL at 8%, **SL fires first** instead.

### SELL order

- **Dust:** if `buy_shares < 5`, no SELL is sent; the position waits for resolution
  (`trader.py:1185`).
- **Order:** GTC limit SELL at `round_to_tick(p)`. It is not marketable and has no re-quote or chase
  (`STRATEGY.md:49`).
- **Size:** `floor6(min(db_shares, clob_token_balance))`.
- **One retry:**
  - **Balance reported:** at the CLOB-reported balance minus 1 micro-share.
  - **Generic balance/allowance error:** at 99%.
  - Source: `trader.py:452-553`.
- **After acceptance:** the trade goes to `PENDING_SELL`.
  - **Exact full fill:** `COMPLETED`.
  - **Partial fill:** back to `HOLDING` with the remainder. A remainder `<= 0.010001` shares is
    treated as quantization dust (`trader.py:712-862`, `821`).
  - **Terminal zero fill:** back to `HOLDING`.

**Realized P&L** is computed only from exact evidence (`trader.py:783-788`):

```
pnl = (sell_vwap - buy_vwap) * sell_size - buy_fee * min(1, sell_size/buy_size) - sell_fee
basis = 'exact_reconciled_buy_sell_confirmed_fills_net_known_fees'
```

**Zero-balance SELL rejection** leads to `_mark_unfilled` (`trader.py:1310-1370`):

- cancel the BUY order, then `UNFILLED`;
- `QUARANTINED` if the BUY order id is missing, or if the order vanished from the CLOB catalog.

There is **no net-positive-after-fee check** on TP. Fees are assumed 0 or maker; see the execution
notes for the fee-evidence rules.

## 5. Sizing and risk

| Parameter | Source default | `config.yaml` | Enforced bound (`_validate_config`, `config.py:170-253`) |
|---|---|---|---|
| `buy_amount_usdc` | 5.0 (`config.py:119`) | 5.0 (`:15`) | `> 0`, `<= max_buy_amount_usdc`, `<= max_open_notional_usdc` |
| `max_buy_amount_usdc` | 100 (`:120`) | 100 (`:19`) | `> 0` |
| `max_positions` | 10 (`:123`) | 10 (`:35`) | `> 0` (no unlimited) |
| `max_open_notional_usdc` | 5000 (`:124`) | 5000 (`:36`) | `>= buy_amount` |
| `max_new_positions_per_cycle` | 1 (`:125`) | 1 (`:37`) | `0 < x <= max_positions` |
| `entry_drawdown_floor_usdc` | -30 (`:126`) | -30 (`:41`) | `< 0` |
| `pending_buy_ttl_minutes` | 30 (`:127`) | 30 (`:45`) | `5..1440` |
| `min_liquidity` | 50000 (`:121`) | 50000 (`:23`) | `>= 0` |
| `max_order_liquidity_ratio` | 0.002 (`:122`) | 0.002 (`:26`) | `(0, 1]` |

Every cycle uses a single fixed size. There is no FOK fallback ladder, no Kelly sizing and no
per-sport sizing.

## 6. Exact-economic entry guard

The guard is `repository.py:433-572` and is evaluated once per cycle (`trader.py:167-178`). It
blocks **only Phase 2/3**; reconciliation and exits always run. It is disabled in simulation.

```
economic = SUM(realized_pnl WHERE pnl_basis = exact_reconciled_...)
         + SUM(settlement_pnl_assumption of RESOLVED rows whose basis is
               'exact_confirmed_buy_remaining_position_net_known_buy_fee' and whose
               stored size/vwap/fee re-match the ledger exactly and recompute to within 1e-6)
```

It blocks if any of these hold:

- `economic <= floor`
- `unknown_buy_evidence > 0`. This counts untracked BUY submissions **plus every open trade whose
  BUY lacks a reconciled matched fill**. Any in-flight `PENDING_BUY` therefore blocks new entries
  until it resolves.
- `incomplete_fee_evidence > 0`
- `resolution_evidence_gap > 0`

## 7. Current live A/B variants

Source contains **no variant names**. The variants are created only by Jenkins env plus
`--job <runtime>`. Evidence:

- `../docs/retro/2026-09-13-golden-cherry-live-ab-preregistration.md:13-31`
- `README.md:7-12`
- local-only `../docs/local/jenkins-job-strategy-inventory.md` §"2026-09-12 16:39 UTC", which is
  also the latest Cherry entry there

| Arm | Jenkins job | `--job` (DB `data/<job>/trades.db`) | Entry band (`buy_threshold`–`sell_threshold`) |
|---|---|---|---|
| A Yellow | `polybot-yellow` | `cherry-live-yellow-076-078-v1` | 0.76–0.78 |
| B Blue (ex-`polybot-cherry`) | `polybot-blue` | `cherry-live-blue-080-082-v1` | 0.80–0.82 |

Common treatment for both arms (prereg `:21-31`):

| Setting | Value |
|---|---|
| `buy_amount_usdc` | 5 |
| `take_profit_percent` | 0.20 |
| `stop_loss_percent` | -0.08 |
| `trailing_stop.percent` | 0.15 |
| `pending_buy_ttl_minutes` | 5 |
| `max_positions` | 10 |
| `max_open_notional_usdc` | 50 |
| `max_new_positions_per_cycle` | 1 |
| `entry_drawdown_floor_usdc` | -30 |
| Universe liquidity | 125,000 (prereg `:10`) — presumably `POLYBOT_MIN_LIQUIDITY=125000`; unverified |
| Cumulative volume | 5,000 |
| Entry window | `(0, 120]` h |
| Timing | pregame and in-play enabled |
| YES-only | yes |
| Schedule | `4-59/5 * * * *` |
| Automatic end date | none |

Additional operational facts:

- **Yellow legacy runtime:** Yellow first runs its legacy `default` runtime as `close_only` with
  TP 0.10 / SL -0.08 / trailing 0.05 each cycle (prereg `:33-36`).
- **Yellow floor:** the inventory records a later operator override of Yellow's floor to
  `POLYBOT_ENTRY_DRAWDOWN_FLOOR_USDC=-200` for the `default` runtime (inventory line ~564; README
  `:17-20`).
- **Wallets:** the arms use separate wallets. Yellow's funded account is the "golden-banana" wallet
  (`AGENTS.md` table).

## 8. Shadow experiment (accountless; GET-only; not live)

The shadow is `main.py --shadow` → `shadow/cli.py`, configured by `shadow_config.yaml`. Its identity
is **frozen by validators** in `shadow/config.py:236-362`: job, contract, prereg id, cadence 5m,
budget 240s, Gamma/CLOB envelopes, bands, policies and dates.

### Universe (`shadow/collector.py:159-270`)

- open, tradable, with a unique event id;
- aligned outcome/price/token arrays;
- `liquidity >= 125000` and `volume >= 5000`;
- sports: pregame `0 < h <= 120` against `gameStartTime`, or in-play;
- non-sports: `0 < h <= 120` against `endDate`;
- **index-0** Gamma probability in `[0.75, 0.88]`.

### Entry price

`walk_asks` (`collector.py:117-134`) walks the full ask book for $5 notional:
`vwap = 5/shares`. If depth is insufficient, there is no entry.

### Bands and exit policies

- **Entry bands** (on the ask VWAP, inclusive):
  - `.76–.78` control
  - `.80–.82` primary
  - `.84–.86` control

  These are `shadow_config.yaml:37-49`.
- **Episode key:** one episode per `(condition, token, band)`.
- **Exit policies** (`shadow_config.yaml:50-85`), evaluated in `_policy_trigger`
  (`collector.py:323-340`) on the **full-depth bid VWAP for entry_shares** (`walk_bids`), with the
  same SL → TP → trailing order:
  - `hold_to_resolution`
  - `tp10/sl08/tr05` current control
  - `tp05`, `tp15`, `sl05`, `sl12`
  - `no_trailing`

### Windows

- Entry window `[2026-09-04T16:00Z, 2026-10-04T16:00Z)`.
- Follow-up ends 2026-11-03.

## 9. Environment and CLI overrides

Precedence is env > `config.yaml` > dataclass default (`config.py:18-44`).

- **Integer params:** YAML must be numeric and ints must be ints.
- **Tunable env vars:**

  | Group | Env vars |
  |---|---|
  | Entry band | `POLYBOT_BUY_THRESHOLD`, `POLYBOT_SELL_THRESHOLD` |
  | Sizing and caps | `POLYBOT_BUY_AMOUNT`, `POLYBOT_MAX_BUY_AMOUNT_USDC`, `POLYBOT_MIN_LIQUIDITY`, `POLYBOT_MAX_ORDER_LIQUIDITY_RATIO`, `POLYBOT_MAX_POSITIONS`, `POLYBOT_MAX_OPEN_NOTIONAL_USDC`, `POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE` |
  | Guards | `POLYBOT_ENTRY_DRAWDOWN_FLOOR_USDC`, `POLYBOT_PENDING_BUY_TTL_MINUTES` |
  | Exits | `POLYBOT_TAKE_PROFIT`, `POLYBOT_STOP_LOSS`, `POLYBOT_TRAILING_STOP_ENABLED`, `POLYBOT_TRAILING_STOP_PERCENT` |
  | Timing | `POLYBOT_TIME_BASED_ENABLED`, `POLYBOT_ENTRY_HOURS_MAX`, `POLYBOT_ENTRY_HOURS_MIN`, `POLYBOT_EXIT_HOURS` |
  | Game start | `POLYBOT_GAME_START_FILTER_ENABLED`, `POLYBOT_ALLOW_IN_PLAY`, `POLYBOT_REJECT_SPORTS_WITHOUT_GAME_START` |
  | Modes | `POLYBOT_YES_ONLY`, `POLYBOT_LIFECYCLE_MODE` (`active`/`close_only`/`archive_only`) |

  Sources: `config.py:291-453`, `:70-87`.
- **Account env:** `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS` (values
  `[REDACTED]`), and `POLYMARKET_SIGNATURE_TYPE` ∈ {1, 3}, default 1 (`config.py:457-474`,
  `:252-253`).
- **Operational env** (`AGENTS.md` "환경변수"): `POLYBOT_DB_MAINTENANCE` (must be `compact-v1` if
  set), `POLYBOT_DB_*` retention knobs and `GIT_COMMIT`, which is the cohort key.
- **CLI:** `polybot run|status|config`, with these flags (`main.py:46-82`):
  - `--config`
  - `--job` (DB dir)
  - `--simulate` (`data/<job>/trades_sim.db`)
  - `--yes-only`
  - `--verbose`

  `main.py --shadow` routes to the shadow.
- **`excluded_categories`** is YAML-only; there is no env var.

## 10. Optimizer search space

"Enforced" means the bound comes from `_validate_config` (`config.py:170-253`). Everything else is
**suggested**, with the reason given.

| Param | Enforced | Suggested search range | Reason |
|---|---|---|---|
| `buy_threshold` / `sell_threshold` | `0 < lo < hi < 1` | lo ∈ [0.70, 0.88], width ∈ [0.02, 0.06] | The hypothesis region is 75–92%. Also keep `hi < 1/(1+TP)` so TP stays reachable (`STRATEGY.md:104`). Narrow bands match the live and shadow design. |
| `take_profit_percent` | `(0, 10]` | [0.05, 0.25] | Useful range is capped by `1/entry - 1`. Shadow grid is 0.05/0.10/0.15/0.20. |
| `stop_loss_percent` | `(-1, 0)` | [-0.15, -0.04] or disabled (hold) | Shadow grid is -0.05/-0.08/-0.12. 5-minute polling plus GTC produced realized SL of -18% to -25% vs a -8% trigger in the 09-13 retro, so model the slippage. |
| `trailing_stop.percent` | `(0, 1)` + enabled flag | {off} ∪ [0.05, 0.20] | Interacts with SL ordering (§4). |
| `entry_hours_max` | `> entry_hours_min >= 0` | [24, 168] | Live 120, shadow frozen 120. |
| `entry_hours_min` | `>= 0` | [0, 12] | |
| `exit_hours` | `0 <= x < entry_hours_max` | {0} ∪ [1, 24] | Disabled live; applies to `endDate` only. |
| `allow_in_play` | bool | {true, false} | All seven live A/B fills were in-play (09-13 retro). |
| `yes_only_mode` | bool | {true, false} | |
| `buy_amount_usdc` | `> 0`, `<= caps` | [5, 50] | Fill rate collapses with size ($100: 66.6% → $3000: 7.6%, `STRATEGY.md:89`). Raising it also silently raises `effective_min_liquidity`. |
| `min_liquidity` | `>= 0` | [50k, 250k] | Live 125k. |
| `max_order_liquidity_ratio` | `(0, 1]` | [0.0005, 0.005] | |
| `max_positions` | `>= 1` | [5, 20] | Counts pending and quarantined rows. |
| `max_new_positions_per_cycle` | `1..max_positions` | [1, 3] | |
| `pending_buy_ttl_minutes` | `[5, 1440]` | [5, 30] | Live 5; the 5-minute cadence makes the effective TTL ~10 minutes. |
| `entry_drawdown_floor_usdc` | `< 0` | risk-policy, not optimized | This is a kill switch, not a signal parameter. |
| cumulative volume floor | hard-coded 5000 (`scanner.py:20`) | [1k, 50k] | Would need to become a parameter. |

## 11. Porting pitfalls

- **Units.** Scan price is Gamma `outcomePrices`, order price is the CLOB midpoint, and P&L uses the
  confirmed VWAP. Keep the three separate.
- **`rapid_jump` is permanent.** A skip is a permanent per-market exclusion, not a per-cycle one.
- **No sort.** Candidate priority is Gamma keyset order. With `max_new_positions_per_cycle=1`, this
  decides which market gets bought.
- **Blocking entries.** Any in-flight PENDING_BUY blocks all new entries through the guard's
  `unknown_buy_evidence`.
- **`RESOLVED` is not cash.** It is a settlement *assumption*. Cherry has no redeem path; the
  redeem/cash credit is separate account-level evidence (`AGENTS.md` item 4).
- **Legacy quarantine.** Legacy `SUBMIT_OUTCOME_UNKNOWN` intents with no order id block the same
  token/side forever until an operator resolves them (`AGENTS.md` item 6). See the execution notes.
- **Known gaps.** `market_snapshots` is never written, so there is no live price-path history
  (`STRATEGY.md:111`). Use the shadow DB or the shared recorder for backtests.
