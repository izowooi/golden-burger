# Golden Plum — porting spec

- Source: `golden-plum/` **per source at a21db7c** (working tree clean for this project at time of reading).
- Authority order used: `src/polybot/config.py` `RUNTIME_SPECS` + `SPORT_PARAMETER_PROFILES` (frozen, fail-closed validated) > `STRATEGY.md`/`AGENTS.md` 2026-09-26 header > `config.yaml` (YAML `trading.entry.*` price/TP/SL values are **ignored**; only shape is checked, `polybot-observability/.../config_contract.py:24`) > README.
- "Live values" below are per source. The operational truth remains the runtime DB resolved config + Jenkins effective config (t1 `AGENTS.md`). Older lines in `AGENTS.md`/`STRATEGY.md` still say soccer King TP `.85`; source and tests say King soccer TP is now `.90` (`config.py:358`, `tests/test_config.py:54`).
- Shared order/fill/fee/reconciliation/resolution mechanics: see [execution-legacy-notes.md](execution-legacy-notes.md). This file restates only strategy-level thresholds and triggers.

## 1. Hypothesis

Take the in-play game's **unique current midpoint leader** among the direct outcome tokens. When its executable `$5` ask VWAP sits in a narrow high-probability band (`.70–.73`), the market still under-prices how often that leader keeps rising. The strategy is momentum/favourite continuation, not a longshot fade.

- The original design (v1/v2) needed a *first upward crossing* confirmed by a 3-observation trend. v9 and later removed every trend/crossing/pullback gate. Entry is now a single current-quote price band (`STRATEGY.md:150-158`, `scanner.py:157-165`).
- Soccer takes the leader across **six** direct books: YES and NO for HOME, DRAW and AWAY. The leader can therefore be a **NO token** (for example NO-on-DRAW or NO-on-underdog). There is no synthetic complement.
- `STRATEGY.md` notes that the max of the six books is structurally at least about `0.67`, because the least-likely outcome's NO is always high. A soccer entry band below about `.67` never triggers.

## 2. Runtime variants (A/B) — per source a21db7c

All variants share these settings: cadence 60 s, exact `$5` baseline, adaptive FOK, `max_positions=10`, event cap 1, 5 new per cycle, 10 SELL POSTs per cycle.

| Jenkins job | runtime_job (`config.py`) | sport | lifecycle | profile key | entry VWAP band | TP (abs) | SL (entry − Δ) | entry source-minute max | forced exit minute | buy $ | loss limit $ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| polybot-king (A, "King") | `plum-live-king-90-1m-v1` (`:352-373`) | soccer | active | `soccer_live_retune` (`:269-273`) | .70–.73 | **.90** | **.12** | 60 | 65 | 5 | 300 |
| polybot-queen (B, "Queen") | `plum-live-queen-95-1m-v1` (`:374-395`) | soccer | active | `soccer_queen_sl17_v16` (`:274-278`) | .70–.73 | **.90** | **.17** | 60 | 65 | 5 | 300 |
| polybot-king | `plum-live-king-nfl-85-1m-v15` (`:396-417`) | nfl | active | `nfl_live_v15` (`:279-287`) | .70–.73 | **.85** | .12 | none | none | 5 | 300 |
| polybot-queen | `plum-live-queen-nfl-90-1m-v15` (`:418-439`) | nfl | active | `nfl_live_v15` | .70–.73 | **.90** | .12 | none | none | 5 | 300 |
| polybot-king | `plum-live-king-mlb-90-1m-v1` (`:440-457`) | mlb | **close_only** | `mlb_live` (`:297-316`) | (.55–.58, 5 obs, +.01) | .65 | .12 | none | none | 5 | 10 (default `:343`) |
| polybot-queen | `plum-live-queen-mlb-95-1m-v1` (`:458-475`) | mlb | **close_only** | `mlb_live` | (.55–.58) | .70 | .12 | none | none | 5 | 10; guard opt-out allowed (`:1140-1141`) |
| polybot-silver | `plum-shadow-silver-1m-v1` (`:476-493`) | soccer | sim | `soccer` (`:238-268`) | .70–.73 | .95 | .15 | 75 (implicit soccer default `:1186`) | 75 | 5 | n/a |
| polybot-gold | `plum-shadow-gold-{mlb,nfl,nba,nhl}-1m-v1` (`:494-565`) | each | sim | `<family>` (`:249-262`) | .75–.78 | .95 | .15 | none | none | 5 | n/a |

Notes:

- **Soccer A/B (v18, 2026-09-26):** the only treatment is the stop delta, `.12` versus `.17`. TP is `.90` for both arms (`research/frozen-2026-09-26-soccer-sl12-17-ab-v18/PREREGISTRATION.md`). The runtime names' `90`/`95` suffixes are historical identifiers.
- **NFL A/B:** the only treatment is TP, `.85` versus `.90`.
- **MLB:** close-only. It reconciles and exits existing holdings and never scans for entries (`bot.py:351`).
- **Silver/Gold:** credential-free simulation and raw collectors. Supplying `POLYMARKET_*` env raises (`config.py:1568-1572`). Their `protocol_id` is rewritten to `plum-<family>-price-band-shadow-v9` (`config.py:576-582`). Gold's hard deadline is 90 s (`:572-574`) and Silver's is 50 s.
- **Holdings keep the rules from their BUY.** `take_profit_price_at_buy`, `stop_loss_delta_at_buy` and `force_exit_minute_at_buy` are persisted at BUY (`trader.py:1100-1106`). Exits use these stored values, not the current config (`trader.py:474-491`, `:508-510`).
- Variant naming across repos: A=Cat/Eco/King, B=Dog/Fruit/Queen (v18 prereg).

## 3. Market universe

### 3.1 Gamma discovery (`api/gamma_client.py:271-449`)

- Endpoint: `GET https://gamma-api.polymarket.com/events/keyset`.
- Params: `limit=500`, `closed=false`, `live=true`, `tag_id=<family tag>`, `related_tags=false`, `liquidity_min=5000`, `volume_min=5000`, `after_cursor`.
- Page cap: soccer 4 pages, direct sports 2 pages (`:40-42`, `config.py:246,257`). If the cursor would exceed the cap, the run raises (fail-closed).
- Family tag ids (`config.py:85-90`): soccer `100350`, MLB `100381`, NBA `745`, NFL `450`, NHL `899`. Esports `64` is always excluded.

A market qualifies (`gamma_client.py:157-225`) only if all of these hold:

- The classifier accepts the event.
- `parentEventId` is empty.
- The event has `active=true`, `closed=false`, `live=true`, `ended=false`.
- The game start (`market.gameStartTime` → `event.startTime`/`eventDate`/`startDate`/...) is at or before now. There is no upper age limit (`SPORT_FAMILY_MAX_IN_PLAY_HOURS` is `None`, `config.py:131-140`).
- The market has `active`, `!closed`, `enableOrderBook` and `acceptingOrders`.
- `match_result_reason == ok`.

### 3.2 Soccer league classifier (`league_classifier.py:337-446`, identities `config.py:643-683`)

These fields must match exactly: `sport.id`, `sport.name`, `sport.primaryTagId`, `sport.series`, the event `series[].id`/`slug`, and `seriesSlug`. The event tags must include the common tags `{1, 100639, 100350}` plus the league's required tags. There must be exactly 2 `teams`, and every team's `league` must equal the code.

| code | name | sport_id | primaryTag | series_id / slug | required tags |
|---|---|---|---|---|---|
| epl | Premier League | 2 | 306 | 10188 / premier-league-2025 | 82, 306 |
| bun | Bundesliga | 7 | 1494 | 10194 / bundesliga-2025 | 1494 |
| fl1 | Ligue 1 | 11 | 102070 | 10195 / ligue-1-2025 | 102070 |
| lal | LaLiga | 3 | 780 | 10193 / la-liga-2025 | 780 |
| mls | MLS | 33 | 100100 | 10189 / mls-2025 | 100100 |
| sea | Serie A | 12 | 100618 | 10203 / serie-a-2025 | 101962 |
| unl | UEFA Nations League | 297 | 100816 | 11446 / soccer-unl | 100816, and slug `unl-x-y-YYYY-MM-DD` with resolution host `www.uefa.com` |

The UEFA cups are routed by tag id. The event tags must include the common tags plus the cup tag, the series must match, the event slug must carry the prefix, `resolutionSource` must have host `www.uefa.com`, and there must be 2 teams:

- UCL: tag 100977, series 10204/`ucl-2025`, prefix `ucl-`.
- UEL: tag 101787, series 10209/`uel-2025`, prefix `uel-`.

The docs say "8 competitions". The source accepts **9**, because UNL is also accepted.

Statuses:

- `REJECTED`: league not allowed, or esports.
- `DRIFT`: a known league with any metadata mismatch.
- **Any `DRIFT` exclusion in a sweep blocks all new entries that cycle**, via `league_identity_metadata_drift` (`bot.py:262-274`, `:518-519`).

### 3.3 Direct US sports classifier (`league_classifier.py:130-257`)

Identities are defined at `config.py:118-123`:

| sport | sport_id | root series | team_league |
|---|---|---|---|
| MLB | 8 | 3 | mlb |
| NBA | 34 | 10345 | nba |
| NFL | 10 | 10187 | nfl |
| NHL | 35 | 10346 | nhl |

Checks:

- These must match exactly: `sport.id`, `sport.sport`, `sport.name`, `primaryTagId`, and `sport.series` equal to the root series id.
- NFL exception: season series `12185` is accepted when the event `seriesSlug == "nfl-2026"` and the event series ids are `("12185",)` (`:188-198`).
- The common tags `{1, 100639, primary}` must appear on both the event and the sport.
- There must be exactly one event series, with `seriesType == "single"` and `recurrence == "daily"`. That series is either the root series (slug = code, ticker = code, title = name) or `<code>-YYYY` with title `"<NAME> YYYY"` and a schedule year of Y or Y+1.
- There must be 2 teams, both with the family's `league`.
- A regex excludes minor leagues and non-major play: `milb|minor league|g league|summer league|ahl|echl|ncaa|college|u-2x|reserve|academy` (`:25-29`).
- Postseason games are accepted because they share the same season series.

### 3.4 Whole-game moneyline identification (`strategy/filters.py:68-278`)

- The market has exactly 2 outcomes, prices and token ids, the tokens are distinct, and prices lie in `[0,1]`.
- **Soccer:** outcomes must be exactly `["Yes","No"]` and `negRisk is True`.
- **US:** outcomes must be team labels (not Yes/No) and `negRisk is False`.
- `sportsMarketType == "moneyline"`. `parentMarketId` is empty, and none of `isFuture/isProp/isAdvancement` is true.
- Exclusion regexes run over `groupItemTitle + question + slug`:
  - series winner / World Series winner
  - `draw no bet` / `dnb`
  - half, quarter, period or inning markets
  - spread, handicap, total, over/under
  - advance or qualify
  - penalties, corners, shots, goalscorer, touchdowns, runs
  - puck line, run line
  - futures, season-long, and championship/conference/division winner
- The event must be unique, `parentEventId` empty, with exactly 2 teams and unambiguous team name/alias/abbreviation forms.
- **US:** each outcome label must map to exactly one team → `DIRECT_TWO_TEAM` (HOME = `teams[0]`, AWAY = `teams[1]`).
- **Soccer settlement scope (`:117-185`):**
  - The description must contain *"this market refers only to the outcome within the first 90 minutes of regular play plus stoppage time"*.
  - Any clause that mentions extra time or penalties must be an explicit exclusion; otherwise the market is rejected as contradictory.
- **Soccer result kind:** taken from normalized `groupItemTitle`.
  - `DRAW` if the title is `draw`/`tie`, or `draw|tie <home> vs|v <away>`.
  - `HOME` if it matches a home team form, `AWAY` if it matches an away team form.
- **Draw handling:** DRAW is a first-class proposition. Both its YES and its NO tokens are candidates for leader, so buying NO-DRAW is possible.

### 3.5 Complete event set (`scanner.py:364-536`, `:994-1048`)

- Soccer needs exactly 3 condition ids (HOME/DRAW/AWAY) and 6 distinct tokens, with identity set `{H,D,A}×{YES,NO}`. US sports need 1 condition and 2 tokens, with identity set `{HOME,AWAY}×DIRECT`.
- Every token needs a full `$5` ask walk (`_walk_buy_book` raises if `$5` of displayed ask depth is missing) **and** a non-empty bid side.
- If any token is missing, the event is `incomplete_direct_book_coverage` and no entry is made.

## 4. Per-cycle data inputs

1. The Gamma keyset sweep above.
   - Event fields used: `id`, `slug`, `active`, `closed`, `live`, `ended`, `startTime`/`eventDate`/`startDate`, `elapsed`, `period`, `score`, `sport{id,sport,name,primaryTagId,series,tags}`, `tags[]`, `series[]`, `seriesSlug`, `teams[{name,alias,abbreviation,league}]`, `resolutionSource`, `parentEventId`.
   - Market fields used: `conditionId`, `outcomes`, `outcomePrices`, `clobTokenIds`, `negRisk`, `sportsMarketType`, `parentMarketId`, `groupItemTitle`, `question`, `slug`, `description`, `liquidity(Num)`, `volume24hr`, `active`, `closed`, `enableOrderBook`, `acceptingOrders`, `gameStartTime`, `updatedAt`, `umaResolutionStatus`.
2. A CLOB full order book for every candidate token, fetched once per cycle as a batch (`clob.get_buy_book_walks(token_ids, notional_usdc=5)`, `scanner.py:617-619`). Books are cached; the raw JSON is kept as evidence.
3. The same books are re-fetched fresh just before POST (`trader.py:865-930`).
4. Holding books are batch-read via `get_sell_book_walks`, and each is re-read fresh after the lifecycle preflight (`bot.py:305-337`, `trader.py:2672-2690`).
5. Stops and TPs need a lifecycle preflight: Gamma event+market live **and** CLOB `get_market_resolution(condition).status == "OPEN"` (`trader.py:2980-3053`).
6. Follow-up of conditions that vanished from discovery: `GET /markets?condition_ids=` first, with a CLOB market fallback (`scanner.py:785-897`).

### Source minute (soccer only) — `scanner.py:264-328`

`get_source_regulation_minute(event)` decodes `event.elapsed`: numbers, `"MM:SS"`, `"H:MM:SS"`, `"90+N"` → 90+N, with a trailing `'`/`min` stripped. The result then depends on `event.period` (case-folded):

- `ht`/`half time`/`halftime` → **45.0**
- `ft`/`full time`/`fulltime` → **90.0**
- `1h`/`first half`/`1`/`first` → elapsed
- `2h`/`second half`/`2`/`second` → `45+elapsed` if elapsed < 45, otherwise elapsed. This handles feeds that restart the clock at the half.
- Any other period → `None` (`SOURCE_PERIOD_UNSUPPORTED`)

Rules around the source minute:

- A missing or invalid elapsed gives `None`, and soccer entry is rejected (`source_clock_required=True`).
- Wall clock is never substituted.
- MLB/NBA/NFL/NHL always get `None` (`SOURCE_CLOCK_NOT_COMPARABLE_<SPORT>`). Innings and quarters are never converted.
- There is no "tick0" concept in Plum. Time is only `source_elapsed_minutes`, plus the snapshot UTC used for trend gap checks.
- The exit path reuses the current-cycle market's event. If that is absent, it falls back to `GET /events/{id}` (`trader.py:433-458`).

## 5. Entry rules (exact order)

Scan happens in `scanner.scan_buy_candidates` (`scanner.py:899-1214`), then execution in `trader.execute_buy` (`trader.py:690-1123`).

1. The entry period `experiment_start_utc ≤ now < experiment_entry_end_utc` must hold. For soccer and NFL live the end is year 9999.
2. The market is eligible: whole-game moneyline, event active and live and not ended, and a game start is known.
3. For soccer, the source minute must not be `None`, must be `≥ 0`, and must satisfy `source_minute ≤ max_source_minute (60) + 1e-9`. The comparison is `>` for rejection, so **minute 60.0 exactly is still allowed**; the docs say "<60".
4. The event set is complete (§3.5).
5. **Leader:**
   - Each token's `midpoint = (best_bid + best_ask)/2` is taken from the full book top of book (`scanner.py:1026`).
   - Tokens are sorted by `(-midpoint, candidate_kind)`.
   - The margin between the leader's midpoint and the second's must be `≥ 0.005` (`min_leader_margin`).
6. The leader's top-of-book spread `best_ask − best_bid` must be `≤ 0.05` (`max_entry_spread`).
7. **Entry price = exact-$5 ask VWAP:** `vwap = 5 / shares_bought_walking_asks`, computed from the full displayed ask ladder (`clob_client.py:231-263`). It must satisfy `0.70 ≤ vwap ≤ 0.73` with ±1e-9 tolerance.
8. **Trend gate:**
   - With `trend_observations=1` it reduces to the band check on the current snapshot (`scanner.py:157-165`).
   - Historical profiles (MLB `mlb_live`: 5 obs, cumulative move ≥ +.01, pullback ≤ 0, gap ≤ 90 s, prior < prob_min ≤ current ≤ prob_max) use the full first-cross logic at `scanner.py:166-206`.
9. **One per event/token:**
   - `claim_entry_episode` is unique per token. Only `PROVEN_ZERO_FILL_RETRYABLE` states may be retried (`repository.py:1459-1500`).
   - `can_reenter` refuses any event with a prior trade in PENDING_BUY, HOLDING, PENDING_SELL, COMPLETED, RESOLVED or QUARANTINED (`repository.py:284-322`). **An event is never re-entered after an exit.** `reentry_cooldown_hours=720` is validated but unused.
10. Cycle-level entry guards (`bot.py:351-620`) block all entries on any of these:
    - economic drawdown `≤ −drawdown_loss_limit` (DB-wide confirmed SELL P&L + proven resolution P&L, `repository.py:2925+`)
    - an economic P&L evidence gap
    - a live HOLDING missing BUY fill/fee evidence
    - a QUARANTINED position
    - `total_reserved ≥ 10`
    - an unknown-side reconciliation error
    - classifier DRIFT
    - `POLYBOT_ACCOUNT_PRIOR_CHILD_FAILED=1`

    At most `max_new_positions_per_cycle=5` BUYs are submitted per cycle.
11. **Pre-POST revalidation (`trader.py:733-930`):**
    - re-run the trend and lineage check
    - capacity (`open + untracked ≤ 10`) and event count `< 1`
    - the entry period again
    - the in-play hours
    - the source clock and minute window, recomputed from the current-cycle market
    - **a fresh fetch of all 6 (or 2) books**; the leader must be the same token with margin ≥ .005, spread ≤ .05 and VWAP in the band.
12. **Size:**
    - `select_adaptive_buy_from_book_evidence` (`clob_client.py:266-333`) picks the largest amount in `{target, 5} ∪ ladder∩[5,target]` whose full walk is fillable **and whose worst consumed ask (`limit_price`) ≤ prob_max (.73)**.
    - The ladder is 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200, 250, 500, 750, 1000 (`config.py:64-84`).
    - At target `$5` this is just `$5`, or nothing.
    - The share count must be at least `min_order_size=5`.
    - A FOK BUY is submitted as `place_fok_buy(amount_usdc, limit_price=walk.limit_price, max_limit_price=.73)`. See the execution notes.
13. The trade is stored as `PENDING_BUY` (live) or `HOLDING` (sim). It records `stop_price_at_entry = max(0.01, walk.vwap − Δ)` and the stored TP/SL/force-exit values.

## 6. Exit rules

Exits are evaluated every cycle for each HOLDING (`trader.execute_sell`, `trader.py:2581-2978`). Thresholds come from the values stored at BUY (§2).

- **Entry reference:** `buy_confirmed_vwap`, falling back to `buy_price`.
- **Stop trigger:** `max(0.01, entry_vwap − stop_loss_delta_at_buy)`.
- **TP:** `take_profit_price_at_buy` as an absolute price (`trader.py:460-496`).

Plan construction from one immutable book (`_exit_plan_from_book_evidence`, `trader.py:520-654`):

1. **time_exit_due** holds when `force_exit_minute_at_buy` is not None and the current source minute is `≥ 65 − 1e-9`. This applies to soccer only; NFL and MLB have None.
2. **Take profit (partial allowed):**
   - This check is skipped if time exit is due.
   - `select_take_profit_sell_from_book_evidence` (`clob_client.py:427-530`) counts only bid levels whose price is individually `≥ TP`, and floors that depth to 0.01 shares.
   - If the profitable depth is below 5 shares, there is no TP this cycle.
   - If it is at least the whole position, the whole position is sold.
   - Otherwise it sells `min(profitable, position − 5)`, floored to 0.01, so the remainder stays ≥ 5 shares. It never strands a sub-minimum residual.
   - The remaining shares stay `HOLDING` on the same Trade.
3. **Stop:**
   - The trigger is best bid `≤ stop_trigger` (a bid-based trigger, not VWAP).
   - The **whole position** must be walkable on displayed bids. If it is not, the stop is recorded as `FULL_STOP_DISPLAYED_DEPTH_UNAVAILABLE`. There is no partial stop; it retries next cycle and quarantines after 180 min.
4. **Time exit:** if due, the whole position is sold via FOK at the full-depth bid walk. Missing depth is recorded and retried in the same way.
5. Coded priority:
   - If the time exit is **not** due: TP slice if available, otherwise the stop when bid ≤ trigger.
   - If the time exit **is** due: TP is skipped, and the full-position FOK is labelled `absolute_stop` when bid ≤ trigger, else `time_exit`.
   - The docs' "target → stop → minute65" matches this, except that TP never runs once minute 65 is reached.

Pre-submission gates for every exit (`trader.py:2667-2760`):

- **Lifecycle preflight:** the Gamma event+market must be explicitly live and accepting orders, **and** the CLOB market must be `OPEN`. If this fails, **no TP and no stop are sent**, and the resolution path runs instead. After the whistle a position therefore holds to resolution.
- **The book is re-read after the preflight** and the plan is recomputed.
- **Safety envelope:**
  - Stop and time exit (`_stop_execution_price_is_safe`, `:3055-3130`):
    - The book must be executable: bid, vwap and limit all in (0,1).
    - The walked shares must equal the signable shares.
    - The spread must be `≤ 0.10` (`max_stop_spread`).
    - The "normal" envelope is vwap and limit `≥ trigger − 0.05` (`max_stop_slippage`) with loss fraction ≤ 1.0. **A gap outside the normal envelope is still allowed if the book is executable** (the Lille–PSG 0.95→0.058 fix).
  - TP (`_profit_execution_price_is_safe`, `:3132-3154`): spread `≤ 0.10` and slice VWAP `≥ TP`.
- **Cycle quota:** `max_emergency_sells_per_cycle = 10` SELL POSTs per cycle. The quota applies only to the POST; holding and resolution checks continue.
- **Order:** SELL is always a FOK limit at `walk.limit_price`, which is the worst consumed bid. Shares are floored to 0.01 (`_sdk_sellable_shares`) and then nudged with `nextafter` (`trader.py:122-158`).
- **No fee or net-positive check** exists at TP. The only condition is that every consumed bid is `≥ TP`.

**Hold to resolution** applies when neither TP, stop nor time exit could execute and the market closes.

- The resolution is proven via Gamma `closed=true` with one-hot `[1,0]`/`[0,1]` outcome prices, or `[0.5,0.5]` with `umaResolutionStatus=="resolved"` (VOID) (`filters.py:414-463`).
- Otherwise the CLOB market resolution (RESOLVED/VOID) is used (`trader.py:1686-1729`).
- It is applied only when the BUY has complete terminal fill+fee evidence (`trader.py:1646-1684`).
- After a partial TP, the resolution P&L adds only the residual-share payout (`STRATEGY.md` "부분 익절").

## 7. Sizing and risk

- **Buy amount:** `$5` for every live runtime (`buy_amount_usdc`). Validated to `$5–$1000` in cent precision, with `5 × max_new_per_cycle ≤ $5000` (`config.py:1099-1126`).
- **Adaptive FOK ladder fallback:** see §5 step 12. It only matters when the target is above $5.
- **Frozen limits (validator):**
  - `max_positions=10`, `max_event_positions=1`, `max_new_positions_per_cycle=5` (`:1119-1124`)
  - `max_emergency_sells_per_cycle=10` (`:1127`)
  - `experiment_capital_usdc=50`, `max_drawdown_stop=0.20` (both informational, `:1129-1132`)
  - `min_order_size=5` shares, buffer 0 (`:1155`)
  - stop floor 0.01, `max_stop_slippage=.05`, `max_stop_spread=.10`, `max_stop_loss_fraction=1.0` (`:1209-1220`)
- **Loss limits:**
  - `$300` for soccer and NFL live (`RuntimeSpec.drawdown_loss_limit_usdc`).
  - The `$10` default applies to MLB close-only, where it is moot because close-only never enters.
  - The guard is **per runtime DB**, not shared across arms. The docs call it "공통 $300", meaning the same value is set in each DB.
  - Only `plum-live-queen-mlb-95-1m-v1` may set `POLYBOT_DRAWDOWN_GUARD_ENABLED=false` (`:1140-1141`).
- **Timeouts:**
  - Delayed FOK reconciliation: 2 min.
  - BUY and SELL unknown-exposure quarantine: **180 min** (`stop_sell_quarantine_timeout_minutes`, also used for BUY, `:1150-1154`).
  - A quarantined row keeps a capacity slot and closes its event (`trader.py:1801-1843`).

## 8. Per-sport differences

| | soccer (live) | NFL (live) | MLB (close-only) | NBA/NHL (sim only) |
|---|---|---|---|---|
| books | 6 (YES/NO × H/D/A), negRisk=true | 2 team tokens, negRisk=false | 2 | 2 |
| source clock | required, minute window [0,60] | none | none | none |
| forced exit | minute ≥65, full FOK | none | none | none |
| entry band | .70–.73 | .70–.73 | .55–.58 + 5-obs first cross (historical) | .75–.78 (shadow) |
| TP / SL | .90 / .12 (King), .90 / .17 (Queen) | .85 (King), .90 (Queen) / .12 | .65 / .70 / .12 | .95 / .15 (shadow) |
| Gamma pages | ≤4 | ≤2 | ≤2 | ≤2 |

## 9. Env / CLI / job overrides

- CLI: `polybot run|status|config --job <runtime_job> [--live|--simulate] [--config config.yaml]` (`main.py:126-157`). `--job` must be a key in `RUNTIME_SPECS`. The mode must equal `RuntimeSpec.simulation_mode`, otherwise the command raises.
- The DB path is `data/<job>/trades.db` for live and `trades_sim.db` for sim (`config.py:1599-1601`). Silver/Gold require the exact external `WORKSPACE` (`main.py:26-76`).
- Env vars that are read but **validated back to frozen values** (so they can only restate them, otherwise the run raises):
  - `POLYBOT_ENTRY_PROB_MIN/MAX`, `POLYBOT_MIN/MAX_SOURCE_MINUTE`, `POLYBOT_TREND_*`, `POLYBOT_MIN_LEADER_MARGIN`, `POLYBOT_MAX_ENTRY_SPREAD`
  - `POLYBOT_TAKE_PROFIT_PRICE`, `POLYBOT_STOP_LOSS_DELTA`, `POLYBOT_FORCE_EXIT_MINUTE`, `POLYBOT_STOP_PRICE`, `POLYBOT_MAX_ENTRY_DRAWDOWN`, `POLYBOT_MAX_STOP_*`
  - `POLYBOT_MIN_LIQUIDITY`, `POLYBOT_MIN_VOLUME_24H`, `POLYBOT_MIN_CUMULATIVE_VOLUME`
  - `POLYBOT_MAX_POSITIONS`, `POLYBOT_MAX_EVENT_POSITIONS`, `POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE`, `POLYBOT_MAX_EMERGENCY_SELLS_PER_CYCLE`
  - `POLYBOT_REENTRY_COOLDOWN_HOURS`, `POLYBOT_*_TIMEOUT_MINUTES`, `POLYBOT_MIN_ORDER_*`, `POLYBOT_YES_ONLY`, `POLYBOT_LIFECYCLE_MODE`, `POLYBOT_SPORT_FAMILY`, `POLYBOT_EXPERIMENT_*_UTC`, `POLYBOT_ENTRY_HOURS_*`, `POLYBOT_ARCHIVE_*`
  - All of these are in `config.py:1287-1552`, with validation at `:980-1262`.
- Env vars that **really change behaviour**:
  - `POLYBOT_BUY_AMOUNT`: any cent value in $5–$1000; it is not tied to the RuntimeSpec.
  - `POLYBOT_DRAWDOWN_GUARD_ENABLED`: Queen MLB only.
  - `POLYBOT_SNAPSHOT_RETENTION_DAYS`: must be ≥ 60.
  - `POLYBOT_ACCOUNT_PRIOR_CHILD_FAILED=1`: blocks entries.
- Credentials: `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`, `POLYMARKET_SIGNATURE_TYPE`.
  - `signature_type` must be in {1, 3} and defaults to 1 (`:1236-1237`, `:1587-1591`).
  - All three are forbidden in sim runtimes.
  - Values: [REDACTED].
- Cohort identity: `config_hash × strategy_source_digest × mode × job_name`. `strategy_source_digest` covers the source plus the preregistration file (`source_digest.py`).

## 10. Optimizer search space

Code-defined research grids come from `_COMMON_EXPLORATORY_GRID.analysis_*` (`config.py:231-235`). These are the replay grid in `scripts/replay_direct_six_book.py` and are the only in-code ranges.

| parameter | current live | bounds | source of bound |
|---|---|---|---|
| entry band low (`prob_min`) | .70 | .55–.80 (grid {.55,.60,.65,.70,.75,.80}) | code grid. For soccer use ≥ .67 (suggested: structural six-book floor per STRATEGY.md) |
| band width (`prob_max − prob_min`) | .03 | .02–.05 (suggested: v9/v14/v18 all used .03; wider lets gaps create late entries, per `config.py:856-858` comment) | suggested |
| TP (absolute) | .90 / .85 | .85–.97 (grid {.85,.90,.95,.97}); validator requires `prob_max < TP < 1` | code grid + validator `:1204-1208` |
| SL delta | .12 / .17 | .05–.20 (grid {.05,.10,.15,.20}); effective trigger floor .01 | code grid + `:1209` |
| trend observations | 1 | {1,2,3,5} | code grid |
| trend min cumulative move | 0 | {0,.01,.02,.03,.05} | code grid |
| trend max pullback | 0 | 0–.02 (suggested: v2 used .01) | suggested |
| min leader margin | .005 | .000–.02 (suggested: must stay >0 to keep "unique leader") | suggested |
| max entry spread | .05 | .02–.10 (suggested: ≤ `max_stop_spread` .10 so the position is exit-safe) | suggested |
| entry max source minute (soccer) | 60 | 45–80 (suggested: history used 75 (v1/Silver) and 60 (v14+)) | suggested |
| forced exit minute (soccer) | 65 | entry_max+0…90, or None (suggested: must exceed entry max; 75 and 65 used historically; None = pure hold) | suggested |
| buy amount | $5 | $5–$1000 cent precision (validator); promotion only after ≥50 closed/arm and 30 common events (AGENTS.md) | validator |
| min liquidity / cumulative volume | 5000 / 5000 | ≥0; frozen per profile | validator (frozen) |

Keep these fixed (execution safety, not alpha): stop slippage .05, stop spread .10, min order 5 shares, max positions 10/1/5, SELL quota 10, quarantine 180 min, the lifecycle preflight, and the no-re-entry-per-event rule.

## 11. Porting notes / gotchas

- The leader is chosen by **midpoint**, but the band applies to the **$5 ask VWAP**, and the FOK limit must be ≤ `prob_max` at every consumed ask level. These are three different prices; keep them separate.
- Soccer candidates include NO tokens. Resolution for a NO token pays on the aligned `outcomePrices[1]`.
- Entry uses the snapshot's pre-fill VWAP for `stop_price_at_entry`, while exits recompute from `buy_confirmed_vwap`. The two can differ after price improvement.
- TP/SL are frozen per trade. A config change must never rewrite open trades.
- Classifier DRIFT is a global entry kill-switch for the cycle.
- A post-game book with a stray 0.001 bid must not trigger a stop. This is why the Gamma live + CLOB OPEN preflight gates every SELL.
