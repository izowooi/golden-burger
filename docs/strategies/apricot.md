# Golden Apricot — porting spec

- Legacy project: `golden-apricot/` (package `polybot`, strategy name `golden-apricot`).
- All values are **per source at `a21db7c`**, which had a clean working tree for `golden-apricot/`. Line refs are `golden-apricot/<path>:<line>` at that commit.
- Live truth is the runtime DB resolved config, run provenance and the Jenkins effective config (t1 `AGENTS.md`). This document reflects the source-frozen contract, not a verified runtime readout.
- Current state comes from the `STRATEGY.md` / `AGENTS.md` headers dated **2026-09-26**. `config.py` frozen maps override `config.yaml` and README.
- Shared execution mechanics are described in [execution-legacy-notes.md](execution-legacy-notes.md) and not restated here:
  - FOK submission
  - book-walk details
  - fee schedule source
  - CONFIRMED fill reconciliation
  - PENDING_BUY/QUARANTINED
  - resolution P&L

## 1. Hypothesis

**Late-game favourite underpricing in MLB.** About 90 minutes after an MLB game becomes live (collector clock, not innings), the team whose midpoint leads the two-token moneyline and whose exact-$5 ask VWAP is 0.90–0.999 is still underpriced relative to its resolution probability. The strategy buys this high-confidence favourite once and exits in one of two ways:
- early, at the first full-holding bid that is ≥ 0.90 **and** net-positive after all fees, or
- otherwise by holding to exact resolution.

This is a favourite-longshot ("favourite is still cheap") bet.
- A loss is a rare large one: the full notional when the favourite loses.
- Wins are small: `1 − entry` minus fees.

The A/B has two arms, and the sole treatment is the entry tick:
- **Eco** (A) enters at 90 minutes.
- **Fruit** (B) enters at 85 minutes.

Both arms run in independent accounts. The same event traded in both wallets is one paired unit, not two independent games (`STRATEGY.md:1-24`, `AGENTS.md:3-15`).

## 2. Live runtimes / variants (frozen in `src/polybot/config.py`)

Only two jobs are registered. `load_config` rejects any other job name (`config.py:123-136`, `config.py:643-646`, `test_config.py` `test_only_registered_jobs_and_live_mode`).

| Field | Eco (A) | Fruit (B) | Source |
|---|---|---|---|
| Jenkins job = runtime name | `apricot-live-eco-mlb-tick90-tp95-v2` | `apricot-live-fruit-mlb-tick90-tp95-v2` | `config.py:38-41` |
| Profile key | `mlb_live` | `mlb_fruit_tick85` | `config.py:130, 299` |
| `profile_version` | `apricot-mlb-tick90-floor90-tp95-live-v2` | `apricot-mlb-tick85-netpositive-live-v10` | `config.py:279, 296` |
| `entry_tick_minute` | 90 | 85 | `config.py:289, 297` |
| `max_source_minute` (window end) | 92 | 87 | `= tick + 2`, `config.py:800, 970` |
| `exit_basis` | `absolute_tp_or_resolution` | same | `config.py:39-40, 985` |
| TP (absolute full-holding bid VWAP floor) | 0.90 | 0.90 | `config.py:125-128` |
| `stop_loss_delta` | 0.99 (effectively disabled; no stop path for this exit_basis) | same | `config.py:131` |
| Entry exact-$5 ask VWAP band | [0.90, 0.999] | same | `config.py:772-774, 962-966` |
| Target buy | $15.00 | $15.00 | `config.py:42, 711-715` |
| `max_positions` | 6 | 6 | `config.py:43, 725-733` |
| `experiment_capital_usdc` (metadata/planned exposure) | 100 | 100 | `config.py:44, 738-744` |
| `drawdown_loss_limit_usdc` (confirmed economic loss) | 300 | 300 | `config.py:45, 747-754` |
| Entry period | [2026-09-10T13:00Z, 9999-12-31) | same | `config.py:34-36, 133-136` |
| Mode | live only (simulation rejected) | same | `config.py:132, 787-791` |
| DB | `data/<job>/trades.db` | same | `config.py:1232-1234` |

- Runtime names still say "tick90-tp95", but both parts are historical.
- Fruit's resolved tick is 85, and the TP floor is now 0.90 plus a net-positive guard (`STRATEGY.md:3, 15-16`).
- A port should treat the arms as `{tick: 90}` vs `{tick: 85}` with everything else identical.

Historical cohorts exist in `config.py:46-122`. They were soccer six-book `net_return`, MLB `absolute_delta` 7pp/10pp TP with 20pp SL, and shadow NBA/NFL/NHL. They are all shadowed out by the overrides at `config.py:123-136` and are not live. Faithful porting of the live strategy needs only the `absolute_tp_or_resolution` path.

## 3. Market universe

### Sport, tag, sweep
- Sport family is `mlb` only for the live jobs (`config.py:129`).
- Gamma tag `100381` (`config.py:159`). Other tags are defined but not live: soccer 100350, NBA 745, NFL 450, NHL 899, esports 64 (`config.py:158-163`).
- Gamma sweep:
  - `GET /events/keyset` with `limit=500, closed=false, live=true, tag_id=100381, related_tags=false, liquidity_min=5000, volume_min=5000`, following the `after_cursor` pagination (`api/gamma_client.py:334-347`).
  - Max 2 pages for direct sports (`gamma_client.py:40-42`, `config.py:287`).
- Market Gamma prefilter:
  - `min_liquidity = 5000`
  - `min_cumulative_volume = 5000`
  - `min_volume_24h = 0`
  - All frozen (`config.py:716-724`).

### League classifier (`league_classifier.py:130-248`, `_classify_direct_sport_event`)
The expected MLB identity is `DirectSportIdentity("mlb", sport_id=8, "MLB", primary_tag_id=100381, root_series_id=3, team_league="mlb")` (`config.py:192`). An event is ACCEPTED only if all of the following hold:
- `event.sport.id == 8`, `sport.sport == "mlb"`, `sport.name == "MLB"`, `sport.primaryTagId == 100381`, `sport.series == 3`.
- Tag IDs `{1, 100639, 100381}` ⊆ the event's tag IDs **and** ⊆ the `sport.tags` CSV (`:151, :190`).
- Exactly one series. That series must have `seriesType == "single"` and `recurrence == "daily"`, and it must match one of:
  - root series `slug == "mlb"` with `id 3`, `ticker "mlb"`, `title "MLB"`, or
  - season series `slug mlb-YYYY` with `ticker == slug`, `title "MLB YYYY"`, and scheduled start year − YYYY ∈ {0, 1} (`:192-228`).
- Exactly two teams, both with `league == "mlb"` (`:229-232`).
- Rejection rules:
  - Reject on the esports tag or slug (`:176`).
  - Reject on the minor-league regex `milb|minor league|g league|summer league|ahl|echl|ncaa|college|u-?2[013]|reserve|academy` over `title/slug/description` (`:25-29, :178`).
- Status handling:
  - Status `DRIFT` means a metadata mismatch without an identity mismatch.
  - Any DRIFT in the cycle blocks all new entries via the `league_identity_metadata_drift` guard (`bot.py:459-460`).
- Postseason games (for example the World Series) stay in the same season series and are accepted. The series-winner market itself is rejected (see below).

### Event/market qualification (`gamma_client.py:190-253`)
- Event conditions:
  - `parentEventId` must be empty.
  - `active == true`, `closed == false`, `live == true`, `ended == false`.
- Game start:
  - `gameStartTime` is taken from the first available of market `gameStartTime` → event `startTime` → `eventDate` → `startDate` → `eventStartTime` → `gameStartTime`.
  - Time since that start must be in `[0, 8h]` (`SPORT_FAMILY_MAX_IN_PLAY_HOURS["mlb"] = 8.0`, `config.py:204-210`).
- Market conditions: `active == true`, `closed == false`, `enableOrderBook == true`, `acceptingOrders == true`.

### Whole-game moneyline identification (`strategy/filters.py`)
- `aligned_binary_reason` (`:68-107`):
  - Exactly 2 non-empty outcome labels, 2 prices in [0,1], and 2 distinct token IDs.
  - For MLB, labels must **not** be `["Yes","No"]` and `negRisk` must be exactly `false`.
- `match_result_reason` (`:205-263`):
  - `sportsMarketType == "moneyline"`, no `parentMarketId`, and no future/prop/advancement flags.
  - Rejects series-winner and World Series winner text (`:15-20`).
  - Rejects `draw no bet`/`dnb`.
  - Rejects the non-whole-game regex (`:21-29`): halves, innings, spread, total, run line, futures, and so on. It is applied over `groupItemTitle + question + slug`.
  - The event must have exactly 2 teams with non-overlapping name/alias/abbreviation forms.
  - Each outcome label (normalized) must match exactly one team. That yields `DIRECT_TWO_TEAM`, with teams[0] = HOME and teams[1] = AWAY (`:248-263, :298-330`).
- Draw handling does not apply to MLB (a two-token book; no draw).
  - The soccer path in the code (HOME/DRAW/AWAY YES+NO six books with a regulation-scope clause) is dormant (`filters.py:264-278`, `config.py:249-259`).
- Expected shape: 1 market and 2 tokens per event, result kinds `{HOME, AWAY}` (`config.py:281-283`). Validation enforces this for the tick jobs (`config.py:823-828`).

## 4. Data inputs per cycle (1-minute Jenkins, single writer per runtime)

1. The Gamma keyset sweep above, plus a sweep attestation that is persisted (`scanner.py:303-305, 428`).
2. The CLOB order book for every qualifying token.
   - Exact-$5 buy walk: `clob.get_buy_book_walks(token_ids, notional_usdc=5.0)` (`scanner.py:318-320`).
   - The full cached book JSON is kept as evidence (`get_cached_book_evidence`).
3. A per-token `MarketSnapshot` row, saved at the cycle reference timestamp, with these fields (`scanner.py:380-414`):
   - `probability = walk.vwap` (the exact-$5 ask VWAP)
   - `midpoint = (best_bid + best_ask) / 2`
   - `best_bid`
   - `best_ask`
   - `spread`
   - `book_json`
4. The DB state:
   - first complete snapshot tick
   - entry episodes
   - trades
   - economic P&L guard
   - order-reconciliation ledger (`polybot_observability`)
5. Holdings: a full-holding sell book walk `get_sell_book_walks({token: sellable_shares})` (`bot.py:266-283`).
   - Before an exit, the bot runs the Gamma market+event lifecycle check and `clob.get_market_resolution(condition_id)` (`trader.py:2313-2388`).

### tick0 / "source minute" derivation (live MLB)
MLB has no minute feed, so the clock is derived from the bot's own durable snapshots.
- **tick0** = `MIN(MarketSnapshot.timestamp)` over timestamps where the event has at least `expected_token_count` (2) distinct token snapshots in the same cycle timestamp. See `repository.get_event_first_complete_snapshot_at` (`db/repository.py:1175-1195`).
  - A snapshot exists only if all of the following hold:
    - the event passed the live and in-play eligibility check,
    - the token had full $5 ask depth,
    - a walk existed.
  - So tick0 is "the first cycle in which this runtime saw both teams' executable $5 books while the event was `live`".
  - Earlier partial ticks, where only one token was saved, are ignored (`test_scanner.py:355`).
- **source minute** = `(now − tick0) / 60 s`, with `clock_reason = FIRST_COMMON_TICK_ELAPSED` (`scanner.py:487-500`). It is recomputed from the same tick0 at order time (`trader.py:615-631`, `test_trader.py:429`).
- **Port caveats:**
  - tick0 is runtime-local. Downtime at game start shifts tick0 later.
  - It is also not synchronized between the Eco and Fruit DBs.
  - The Gamma `elapsed`/`period` fields are recorded as context only and are not used for MLB (`scanner.py:208-231`; a scheduled-start-age clock exists only for the shadow profile).

## 5. Entry rules (exact)

Scan runs in `scanner.scan_buy_candidates` (`scanner.py:449-730`). Fresh revalidation runs in `trader.execute_buy` (`trader.py:510-894`).

1. **Entry period:** `experiment_start_utc ≤ now < experiment_entry_end_utc`, which is [2026-09-10T13:00Z, 9999-…) (`scanner.py:460-468`, `trader.py:590-600`).
2. **Market eligible:** section 3 criteria, including in-play age in `[hours_min = 0, hours_max = 8h]` (`scanner.py:257-288`, `config.py:853-858`).
3. **Timed window:**
   - Conditions: `tick0` exists and `entry_tick_minute ≤ source_minute ≤ entry_tick_minute + 2`.
   - Eco uses [90, 92] minutes; Fruit uses [85, 87] (`scanner.py:504-517`).
   - The +2 slack exists because the 1-minute Jenkins cadence can skip the exact tick (`STRATEGY.md:18-20`).
4. **Complete event book set:** exactly 1 market and 2 tokens with result kinds {HOME, AWAY}. The event is rejected on any duplicate condition (`scanner.py:545-564`).
5. **Per-token book validity for both tokens:**
   - a walk and a snapshot both exist,
   - `best_bid` is not None,
   - `spread = best_ask − best_bid ≤ max_entry_spread = 0.05`.
   - If either token fails, the whole event is rejected (`scanner.py:565-599`, `config.py:806`).
6. **Leader = highest midpoint** `(best_bid + best_ask) / 2`.
   - Ties are broken by `candidate_kind` string.
   - `leader_mid − runner_up_mid ≥ min_leader_margin = 0.005` is required (`scanner.py:600-612`, `config.py:804`).
7. **Price band:** the leader's **exact-$5 ask VWAP** must be in `[prob_min, prob_max] = [0.90, 0.999]` (±1e-9) (`scanner.py:613-622`).
   - "Exact-$X VWAP" = walk asks best-first, spending exactly $X; `vwap = X / shares`; `limit_price` = the worst level touched.
   - The walk fails if the full $X displayed depth is missing (`api/clob_client.py:200-238`).
8. **One-per-event/token:** `repo.claim_entry_episode` is keyed by `token_id`.
   - The first in-band observation is persisted.
   - A later claim succeeds only if the prior episode is proven no-POST: `BLOCKED_GUARD`, `PRE_SUBMISSION_CONTRACT_ERROR`, `QUEUED_NO_POST`, `NO_POST_RETRYABLE`, or `PROVEN_ZERO_FILL_RETRYABLE` after an UNFILLED trade (`repository.py:101-107, 1085-1160`).
   - `can_reenter` blocks the event forever once any trade exists in `PENDING_BUY/HOLDING/PENDING_SELL/COMPLETED/RESOLVED/QUARANTINED` (`repository.py:222-260`). `reentry_cooldown_hours = 720` is metadata only.
   - `max_event_positions = 1` (`config.py:728`).
9. **Candidate ordering:** by `source_minute` ascending, then `event_id` (`scanner.py:718-723`).
10. **Portfolio entry guards** (`bot.py:313-470`). All new BUYs are blocked if any of the following hold:
    - confirmed economic P&L ≤ −$300 (`drawdown_loss_limit_usdc`). This counts confirmed SELL P&L plus proven-resolution P&L, with ledger overrides. Wallet deposits and withdrawals are excluded (`repository.get_economic_pnl_guard` `:1770`).
    - any economic evidence gap
    - a blocking quarantined position
    - an open BUY fill or fee evidence gap
    - an unknown-side reconciliation error
    - league metadata drift
    - reserved capacity ≥ `max_positions = 6`
   
   Per cycle, at most `min(max_new_positions_per_cycle = 5, 6 − reserved)` new exposures are allowed (`bot.py:538-541`). An unknown-outcome submission also reserves a slot (`trader.py:792`).
11. **Fresh revalidation just before the order** (`trader.py:590-760`). Every check is repeated on newly fetched books:
    - in-play window
    - tick window from the same tick0
    - both token books refetched at exact $5
    - both spreads ≤ 0.05
    - the same token is still the midpoint leader with margin ≥ 0.005
    - the leader's $5 VWAP is still in [0.90, 0.999]
12. **Order sizing** (`select_adaptive_buy_from_book_evidence`, `clob_client.py:241-304`, called at `trader.py:720-735`).
    - The ladder is `{target} ∪ {ADAPTIVE_BUY_NOTIONAL_LADDER ∩ [5, target]}`. With target $15 this gives **$15 → $10 → $5**. The ladder comes from `SIMULATION_SCALING_NOTIONALS_USDC` (`config.py:137-157`).
    - The largest amount whose full exact walk exists with `limit_price ≤ prob_max (0.999)` is chosen.
    - The baseline $5 walk must itself satisfy `limit_price ≤ 0.999`.
    - The order needs `shares ≥ min_order_size (5) + buffer (0)` and `0 < limit_price < 1` (`trader.py:740-757`).
    - One FOK BUY is sent (`trader.py:781`, `clob.place_fok_buy(amount_usdc=selected, limit_price=walk.limit_price, max_limit_price=0.999)`). Partial fills are not accepted. See execution notes.
13. The trade is created as `PENDING_BUY` in live mode. It records the entry parameters "at buy" (TP/SL deltas, band, etc.) (`trader.py:827-891`).

## 6. Exit rules (exact) — `exit_basis = absolute_tp_or_resolution`

`Trader._exit_signal` (`trader.py:388-452`) is evaluated every cycle for each HOLDING trade. It is evaluated on the **full-holding** sell walk: `_walk_sell_book` walks bids for `floor2(buy_shares)` shares (`trader.py:85-107`, `clob_client.py:307+`).

- **TP condition.** All of the following must hold:
  - `walk.vwap (full-holding bid VWAP) ≥ take_profit_delta = 0.90`, and
  - net-positive after fees, computed as:
    ```python
    entry_cost   = buy_confirmed_size * buy_confirmed_vwap + buy_confirmed_fee_usdc
    sell_fee     = clob.estimate_taker_fee_usdc(token, shares=walk.shares, price=walk.vwap)
                 # = round_half_up(shares * rate * p * (1 - p)), exponent-1 taker schedule only
    net_proceeds = walk.proceeds - sell_fee
    take_profit iff net_proceeds > entry_cost + 1e-9
    ```
    (`trader.py:424-452`, `clob_client.py:972-991`). The inputs must be finite, size/vwap/cost must be > 0, and fees must be ≥ 0; otherwise there is no signal.
  - The CONFIRMED BUY size, VWAP and fee are required. Requested values are not enough.
- **No stop, no trailing, no forced time exit.** This `exit_basis` never returns `absolute_stop` or `forced_time_exit`. The `late_exit_minute` sentinel is 1,000,000 for direct sports (`config.py:37, 792-798`). `stop_loss_delta = 0.99` is a stored value only.
- **Otherwise the trade is held to resolution.** Resolution is recorded only from exact Gamma `closed == true` token-aligned `[1,0]`/`[0,1]` (or void `[0.5,0.5]`) prices, or from CLOB `RESOLVED` proof (`filters.py:412-453`, `trader.py:1327-1370`). P&L formula: see execution notes.
  - Resolution is checked only when:
    - the full-holding book is unavailable, or
    - an exit signal exists but the live lifecycle is not proven.
  - A losing favourite with a lingering dust bid therefore waits until its book disappears.
- **Pre-submit safety** (`trader.py:2060-2140`). The following checks run before submitting:
  1. **Live-lifecycle proof** (`_stop_execution_is_explicitly_live`, which applies to TP too). If it fails, the resolution check runs instead.
     - Gamma event: `active`, not `closed`, `live`, not `ended`.
     - Gamma market: `active`, not `closed`, `enableOrderBook`, `acceptingOrders`.
     - CLOB `get_market_resolution.status == "OPEN"`.
  2. **Fresh re-walk of the full holding** and re-evaluation of the signal.
  3. **`_profit_execution_price_is_safe`** (`trader.py:2465-2487`):
     - best_bid, vwap and limit_price are all in (0,1),
     - an ask exists (spread is not None),
     - `spread ≤ max_stop_spread = 0.10`,
     - `vwap ≥ 0.90`.
  4. **Per-cycle SELL budget:** at most `max_emergency_sells_per_cycle = 10` submissions (`trader.py:2139-2150`).
- **Order:** one FOK SELL for the full holding at `price = walk.limit_price` (the worst bid touched), size = `nextafter(floor2(shares))`. No slicing (`trader.py:2164-2170`).
- Failed or unknown SELL outcomes auto-quarantine after `stop_sell_quarantine_timeout_minutes = 180`. The BUY reconciliation timeout is also 180; the FOK reconciliation delay is 2 minutes (`config.py:761-767`). Semantics: see execution notes.

## 7. Sizing and risk summary

| Parameter | Value | Enforced at |
|---|---|---|
| Target BUY | $15 (ladder 15/10/5, one FOK) | `config.py:42, 711` |
| Baseline signal notional | $5 exact walk | `config.py:155` |
| `max_positions` (open + reserved) | 6 | `config.py:43, 726` |
| `max_event_positions` | 1 | `config.py:728` |
| `max_new_positions_per_cycle` | 5 | `config.py:729` |
| Per-cycle BUY notional cap | `buy × new_per_cycle ≤ $5000` | `config.py:734` |
| `max_emergency_sells_per_cycle` | 10 | `config.py:736` |
| Economic loss guard | −$300 absolute, shared value per arm (each DB guards itself) | `config.py:45`, `bot.py:341-346` |
| `experiment_capital_usdc` | $100 (with 6 × $15 = $90 max open) | `config.py:44` |
| `max_drawdown_stop` | 0.20 (legacy metadata only) | `config.py:745` |
| `min_order_size` | 5 shares, buffer 0 | `config.py:768` |
| Signature type | 1 or 3 | `config.py:875` |

## 8. Per-sport differences

- Live is MLB only. NBA/NFL/NHL identities and shadow-ready profiles exist (`config.py:191-196, 260-275`), but no live runtime is registered.
- `AGENTS.md`/`STRATEGY.md` forbid expanding to other sports without per-sport tick calibration and a new runtime (`STRATEGY.md:23-24`).
- `max_in_play_hours` defaults: soccer 4, MLB 8, NBA 5, NFL 6, NHL 5 (`config.py:204-210`).
- The soccer six-book path (HOME/DRAW/AWAY × YES/NO, `negRisk == true`, a mandatory "first 90 minutes plus stoppage" settlement clause, and draw identified by `groupItemTitle` "draw"/"tie"/"draw X vs Y") is **dormant** (`filters.py:117-185, 264-278`). Its source minute came from Gamma `elapsed`+`period` (`scanner.py:187-205`).

## 9. Overrides (env / CLI / job name)

- **CLI:** `python -m polybot.main run --job <job> [--live] [--config config.yaml]`.
  - Without `--live`, the bot runs in simulation, and the live jobs reject simulation (`main.py:84-90`, `config.py:787-791`).
  - Other commands: `status` and `config` (resolved-config printout).
- **The job name selects everything:**
  - profile
  - tick
  - TP
  - band
  - amounts
  - dates
- **Env vars** take precedence over YAML, which takes precedence over defaults (`config.py:428-444`). Any env value that differs from the frozen value makes `_validate_config` raise, so env vars cannot change a live value; they can only cause a failure:
  - `POLYBOT_ENTRY_PROB_MIN/MAX`
  - `POLYBOT_MAX_SOURCE_MINUTE`
  - `POLYBOT_MIN_LEADER_MARGIN`
  - `POLYBOT_MAX_ENTRY_SPREAD`
  - `POLYBOT_TAKE_PROFIT_DELTA`
  - `POLYBOT_STOP_LOSS_DELTA`
  - `POLYBOT_LATE_EXIT_MINUTE`
  - `POLYBOT_LATE_PROFIT_FRACTION`
  - `POLYBOT_STOP_CUTOFF_MINUTE`
  - `POLYBOT_STOP_PRICE`
  - `POLYBOT_MAX_ENTRY_DRAWDOWN`
  - `POLYBOT_MAX_STOP_SLIPPAGE/SPREAD/LOSS_FRACTION`
  - `POLYBOT_ENTRY_HOURS_MIN/MAX`
  - `POLYBOT_ARCHIVE_*`
  - `POLYBOT_SNAPSHOT_RETENTION_DAYS`
  - `POLYBOT_YES_ONLY`
  - `POLYBOT_BUY_AMOUNT`
  - `POLYBOT_MIN_LIQUIDITY`
  - `POLYBOT_MIN_VOLUME_24H`
  - `POLYBOT_MIN_CUMULATIVE_VOLUME`
  - `POLYBOT_MAX_POSITIONS`
  - `POLYBOT_MAX_EVENT_POSITIONS`
  - `POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE`
  - `POLYBOT_MAX_EMERGENCY_SELLS_PER_CYCLE`
  - `POLYBOT_EXPERIMENT_CAPITAL_USDC`
  - `POLYBOT_MAX_DRAWDOWN_STOP`
  - `POLYBOT_DRAWDOWN_LOSS_LIMIT_USDC`
  - `POLYBOT_REENTRY_COOLDOWN_HOURS`
  - `POLYBOT_MAX_SNAPSHOT_GAP_MINUTES`
  - `POLYBOT_FOK_RECONCILIATION_TIMEOUT_MINUTES`
  - `POLYBOT_STOP_SELL_QUARANTINE_TIMEOUT_MINUTES`
  - `POLYBOT_MIN_ORDER_SIZE`
  - `POLYBOT_MIN_ORDER_BUFFER_SHARES`
  - `POLYBOT_EXPERIMENT_START_UTC/END_UTC/FOLLOWUP_END_UTC`
  - `POLYBOT_SPORT_FAMILY`
  - `POLYBOT_EXCLUDED_CATEGORIES`
- **`POLYBOT_LIFECYCLE_MODE`** has three values (`config.py:482-496`, `bot.py:240-245, 313, 613-614`):
  - `active` means normal operation.
  - `close_only` runs reconciliation and exits but makes no new entries.
  - `archive_only` does no order or position mutation.
- **Credentials:** `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`, `POLYMARKET_SIGNATURE_TYPE` (default 1). They are required live and forbidden in simulation (`config.py:1198-1224`). Values: [REDACTED].
- **YAML note:** `config.yaml` still carries legacy values (entry band 0.60–0.94, TP 0.03, `buy_amount_usdc: 5`, 2026-08-30 dates). The frozen maps ignore them for the tick jobs (`config.py:962-971, 1072-1074, 1154-1168`), so the YAML is not authoritative.
- **Provenance:** `strategy_source_digest` and `preregistration_sha256` are computed from the source tree (`source_digest.py`) and must be 64-hex (`config.py:889-898`). The cohort key for retros uses `strategy_source_digest`.

## 10. Tunable parameters for an optimizer

Almost every value is frozen by an equality check in `_validate_config`. Only a few general bounds are code-enforced:
- target $5–$1000 in cent precision (`config.py:698-710`)
- `buy × max_new_per_cycle ≤ $5000` (`:734`)
- `retention_days ≥ 60` (`:864`)
- `$5 / prob_max ≥ 5 shares`, i.e. `prob_max ≤ 1.0` (`:866-868`)
- `max_event_positions ≤ max_positions` (`:755`)
- `signature_type ∈ {1,3}` (`:875`)

Everything else below is **suggested** and must be re-registered as a new cohort.

| Parameter | Live | Suggested search range | Reason |
|---|---|---|---|
| `entry_tick_minute` | 90 / 85 | 60–150, step 5 (suggested) | This is the A/B treatment. MLB games run about 3h and in-play max is 8h. Needs per-sport calibration. |
| Tick window width (`max_source_minute − tick`) | 2 | 1–5 min (suggested) | Must be ≥ 1 cadence (60 s + overrun). Wider windows add selection drift. |
| `prob_min` (exact-$5 ask VWAP) | 0.90 | 0.80–0.95 (suggested) | Defines the favourite-bias region. Below that, the loss tail grows. |
| `prob_max` | 0.999 | 0.97–0.999 (suggested; code caps ≤ 1) | Near 1, the post-fee upside is about 0. |
| `min_leader_margin` (midpoint) | 0.005 | 0.0–0.10 (suggested) | Mostly redundant for 2-token books with a 0.90 band. |
| `max_entry_spread` | 0.05 | 0.01–0.10 (suggested) | Stale/thin-book filter, applied to both tokens. |
| TP floor (`take_profit_delta` in absolute mode) | 0.90 | 0.90–0.99 or `resolution_hold` (suggested) | Historical 0.95/0.98/0.99 variants exist (`test_trader.py:276`). The net-positive guard is always applied. |
| Target notional | $15 | $5–$50 (suggested; code $5–$1000) | FOK full-depth success rate falls with size. The ladder covers the fallback. |
| `max_positions` | 6 | 3–12 (suggested) | Concurrent MLB games per slate. Capital = positions × target. |
| `drawdown_loss_limit_usdc` | 300 | operator-set, not optimized | This is a risk authorization, not a strategy parameter. |
| `min_liquidity` / `min_cumulative_volume` | 5000 / 5000 | 1k–20k (suggested) | Server-side prefilter only. The $5 book is the real gate. |

## 11. Port checklist / known subtleties

- The signal uses the **midpoint** for leader selection and the **exact-$5 ask VWAP** for the band. The order uses a **separate adaptive walk** at $15/$10/$5, and its limit is the worst ask touched (capped at 0.999).
- The exit uses the **full-holding bid VWAP**, not the top bid, and requires CONFIRMED BUY size, VWAP and fee.
- The tick0 clock comes from the bot's own complete-snapshot history. A clean port must persist per-cycle, per-token snapshots, or at least the first complete-pair timestamp per event, before evaluating entry in the same cycle. The snapshot write happens before the scan (`bot.py` order: snapshots → Phase 1 exits → Phase 2 scan).
- Entry-episode states make "one attempt per token" idempotent across crashes. They mark the queue as `QUEUED_NO_POST` before any POST (`bot.py:314-327`).
- The economic guard uses confirmed/ledger truth only. Wallet cash flows are excluded.

## 2026-10-05 종목별 설정과 MLB paper 전환

- 대상 종목 MLB·NBA·NHL, 모두 **paper** 5 USDC(yaml `sports` 종목별 매핑, 파라미터 `sport_overrides.<종목>`).
- tick 은 모든 종목에서 **벽시계**(tick0 = 두 팀 호가가 처음 실행 가능해진 분, 사실상 예정 시작) 이후 분이다. NBA·NHL 과거 경기 시계가
  없어 이것만 백테스트할 수 있다. `entry_game_minute`(경기 시계 tick, NBA 0–48·NHL 0–60분)는 opt-in 으로만 있고 기본 null 이다.
- NBA: prob_min 0.94, prob_max 0.99, TP 0.96, eco tick 50 / fruit tick 80. NHL: 같은 값, eco tick 140 / fruit tick 100.
- MLB live→paper: 조기 익절(TP ≤ 0.96) 범위에서 두 반기 모두 0 이상인 조합이 없다. NBA·NHL 통과 조합은 정산 손실 0건일 때뿐이라
  손실 1건이면 반기 손익이 음수가 된다. 손절이 없는 구조의 −100% 꼬리가 문제다(새 가설 필요).
- 계좌 eco·fruit 와 마스터 `mode: live` 는 남은 live 포지션 정리용으로 유지한다.
- 근거 `docs/research/backtests/2026-10-05-per-sport-nba-nhl.md`.
