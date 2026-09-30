# Golden Watermelon Live — porting spec

- Source: `golden-watermelon-live/` **per source at a21db7c** (working tree clean for this project at analysis time, 2026-09-30).
- Authority order used: `src/polybot/config.py` frozen `RUNTIME_SPECS`/`SPORT_POLICIES` > `config.yaml` > README/AGENTS. The 2026-09-26 header of `STRATEGY.md`/`AGENTS.md` describes the current live A/B. Real live values must still be confirmed from each runtime DB's resolved config + Jenkins effective env (t1 AGENTS.md); everything below is "per source".
- Execution mechanics (book walk, FOK submission, fee, fill reconciliation, PENDING/QUARANTINED, resolution P&L) are in [execution-legacy-notes.md](execution-legacy-notes.md). This doc only states strategy-level thresholds and triggers.
- Paths below are relative to `golden-watermelon-live/src/polybot/` unless prefixed.

## 1. Hypothesis

In-play favourite-longshot bias at the top end. Once a whole-game winner token trades at a high *executable* price during the game (baseline exact-$5 ask VWAP ≥ 0.91–0.99), the market still under-prices it: the true win probability is higher than the VWAP after fees. So buy the near-certain winner and hold it to resolution. The only exit is a catastrophe stop. There is no take-profit and no time exit (`STRATEGY.md` "질문과 treatment"; `main.py:175-181`).

## 2. Runtimes, variants and current values

`config.py:127-159` `RUNTIME_SPECS` (runtime_job → Jenkins job, sport family, `prob_min`, policy key). Policies are at `config.py:181-206`. The upper bound is `prob_max = 0.999` for every runtime (`SportPolicy.prob_max`, `config.py:184`); it excludes the terminal price 1.000.

| Account (Jenkins job) | runtime_job | family | entry band (baseline $5 VWAP) | stop floor `stop_price` | policy |
|---|---|---|---|---|---|
| polybot-cat (A "Cat") | `watermelon-live-cat-96-1m-v2h` | soccer | **[0.92, 0.999]** (`config.py:131`) | **0.65** (`config.py:202`) | `soccer` |
| polybot-dog (B "Dog") | `watermelon-live-dog-99-1m-v2h` | soccer | **[0.92, 0.999]** (`config.py:134`) | **0.60** (`config.py:206`) | `soccer_dog_stop60_v7` |
| polybot-cat | `watermelon-live-cat-mlb-96-1m-v4` | mlb | [0.96, 0.999] | 0.70 | `catdog_mlb` (close-only in ops, see below) |
| polybot-dog | `watermelon-live-dog-mlb-99-1m-v4` | mlb | [0.99, 0.999] | 0.70 | `catdog_mlb` (close-only) |
| polybot-cat | `watermelon-live-cat-nfl-91-1m-v6` | nfl | **[0.91, 0.999]** | 0.70 | `catdog_nfl_v6` |
| polybot-dog | `watermelon-live-dog-nfl-94-1m-v6` | nfl | **[0.94, 0.999]** | 0.70 | `catdog_nfl_v6` |
| polybot-bear / tiger | `…-bear-mlb-96-1m-v3a` / `…-tiger-mlb-99-1m-v3a` | mlb | 0.96 / 0.99 | 0.70 | `mlb` (legacy separate wallets) |
| polybot-lion / wolf | `…-lion-nhl-96-1m-v3a` / `…-wolf-nhl-99-1m-v3a` | nhl | 0.96 / 0.99 | 0.70 | `nhl` (legacy separate wallets) |
| (retired) | `…-cat/dog-nfl-96/99-1m-v5` | nfl | 0.96/0.99 | 0.70 | `catdog_nfl` — registered but not in `ACCOUNT_RUNTIMES` |

- **The current soccer A/B has one treatment: the absolute stop floor**, Cat 0.65 vs Dog 0.60. Entry band [0.92, 0.999], $5 and hold-to-resolution are the same for both arms (`STRATEGY.md` 2026-09-26 header). Note that `AGENTS.md`'s "Active contract" table still lists soccer Cat 0.91 / Dog 0.92, and `config.yaml:54` still says `prob_min: 0.91`. Both are stale. `prob_min` comes only from `RuntimeSpec` (the YAML value is ignored; the env var `POLYBOT_ENTRY_PROB_MIN` still overrides), and the validator requires it to equal the spec (`config.py:846-849, 702, 721-725`).
- The Cat/Dog accounts run all three sport children in sequence via `polybot run-account --live --account polybot-cat|dog` (`account_runner.py:14-47`, `ACCOUNT_RUNTIMES` `config.py:163-174`). The child order rotates by `int(time.time()//60) % len(jobs)` (`account_runner.py:28-29`). If any child fails, every later child gets `POLYBOT_ACCOUNT_DISABLE_BUYS=1`: management continues and only BUYs are blocked (`account_runner.py:32-33`, `account.py:149`).
- MLB is close-only in ops through `POLYBOT_CLOSE_ONLY_SPORT_FAMILIES=mlb` (`OPERATIONS.md:30`, applied by `config.py:234-244`).
- Cadence is 1 minute for all jobs (Jenkins timer). Elapsed cycle time is not a signal (`STRATEGY.md` "Cadence와 overlap").

### Effective stop, derived per arm

`effective_stop = round(max(stop_price, confirmed_entry_vwap − 0.30), 6)` (`strategy/trader.py:123-137`).

- **Cat soccer (0.65).** With entry in [0.92, 0.999], `vwap − 0.30` falls in [0.62, 0.699]. The relative leg binds for any entry VWAP above 0.95. The value is recomputed each cycle from the confirmed VWAP (`trader.py:165-167`).
- **Dog soccer (0.60).** `vwap − 0.30 ≥ 0.62 > 0.60`, so Dog's effective stop is always `entry − 0.30`. Dog alone uses the **stored** `stop_price_at_entry` when one exists (`trader.py:160-163`). Holdings opened under the older 0.65 floor therefore keep 0.65; the test is `tests/test_trader.py:1227-1234`.
- **NFL / MLB / NHL (0.70).** `vwap − 0.30 ≤ 0.699`, so the 0.70 floor always binds.

## 3. Market universe

### Gamma sweep, once per cycle

`api/gamma_client.py:242-402`:
- Request: `GET https://gamma-api.polymarket.com/events/keyset` with `limit=500, closed=false, live=true, tag_id=<family tag>, related_tags=false, liquidity_min=5000, volume_min=5000`.
- Up to `MAX_SWEEP_PAGES=4`, following `next_cursor` (`after_cursor`). If the cursor does not advance, or there would be a 5th page, it raises (fail closed).
- Timeouts: connect 2s, read 5s (`gamma_client.py:33-36`).

Family tag IDs (`config.py:58-62, 95-100`):

| family | tag id |
|---|---|
| soccer | 100350 |
| mlb | 100381 |
| nhl | 899 |
| nfl | 450 |

e-sports tag 64 is always rejected.

Server gates are frozen by validator: `min_liquidity=5000`, `min_cumulative_volume=5000`, `min_volume_24h=0` (`config.py:656-664`).

### Per-market qualification

`gamma_client.py:133-196`. **All** of these must hold:
1. League classifier `ACCEPTED` (see below).
2. `event.parentEventId` empty.
3. Event flags: `active=true`, `closed=false`, `live=true`, `ended=false`.
4. Game start is present. It is the first non-empty of `market.gameStartTime`, `event.startTime`, `eventDate`, `startDate`, `eventStartTime`, `gameStartTime`.
5. `0 ≤ now − start ≤ max_in_play_hours`: soccer 4h, mlb 8h, nhl 5h, nfl 6h (`config.py:101-106`).
6. Market flags: `active=true`, `closed=false`, `enableOrderBook=true`, `acceptingOrders=true`.
7. `match_result_reason == ok`.

Any classifier `DRIFT` exclusion in the sweep blocks **all** new BUYs that cycle (`league_identity_metadata_drift`, `bot.py:204-216, 454-455`).

### League classifier

`league_classifier.py`, version `watermelon-major-sports-identity-v1`, mapping hash over `config.py:299-376`.

**Soccer** (`classify_soccer_event`, `league_classifier.py:336-445`).

Domestic leagues are matched on the exact `event.sport.sport` code. The following must all match: `sport.id`, `sport.name`, `primaryTagId`, `sport.series`, and required tags ⊆ both the event tags and `sport.tags`. Common tags are `{1, 100639, 100350}`. The event needs exactly one series with that ID and slug, `seriesSlug` equal to it, 2 teams, and both `team.league == code`.

| code | sport_id | name | primary tag | series id / slug | required tags |
|---|---|---|---|---|---|
| epl | 2 | Premier League | 306 | 10188 / premier-league-2025 | 82, 306 |
| bun | 7 | Bundesliga | 1494 | 10194 / bundesliga-2025 | 1494 |
| fl1 | 11 | Ligue 1 | 102070 | 10195 / ligue-1-2025 | 102070 |
| lal | 3 | LaLiga | 780 | 10193 / la-liga-2025 | 780 |
| mls | 33 | MLS | 100100 | 10189 / mls-2025 | 100100 |
| sea | 12 | Serie A | 100618 | 10203 / serie-a-2025 | 101962 |
| unl | 297 | UEFA Nations League | 100816 | 11446 / soccer-unl | 100816, plus slug regex `unl-x-y-YYYY-MM-DD` and resolutionSource host `www.uefa.com` |

- UNL is in the frozen registry (`config.py:324-327`) even though STRATEGY.md's league list omits it.
- **UEFA cups** are matched by tag, before the domestic check:
  - UCL: tag 100977, series 10204 / `ucl-2025`, slug prefix `ucl-`, resolution host `www.uefa.com`.
  - UEL: tag 101787, series 10209 / `uel-2025`, slug prefix `uel-`, host `www.uefa.com`.
  - Both need the common tags and 2 teams. If both cup tags are present the event is DRIFT (`league_classifier.py:377-405`).
- Unknown sport code → REJECTED `LEAGUE_NOT_ALLOWED`. Partial identity mismatch → DRIFT, which blocks entry globally.

**MLB / NHL / NFL** (`_classify_direct_sport_event`, `league_classifier.py:129-256`). Identities are at `config.py:90-94`:

| family | sport_id | primary tag | root series |
|---|---|---|---|
| mlb | 8 | 100381 | 3 |
| nhl | 35 | 899 | 10346 |
| nfl | 10 | 450 | 10187 |

- Required: sport id, code, name and primaryTag match; `sport.series == root`, except NFL, which also accepts season series `12185` when `seriesSlug=="nfl-2026"` (`:190-197`).
- Tags `{1, 100639, primary}` ⊆ both the event tags and `sport.tags`.
- Exactly one series, `seriesType=single`, `recurrence=daily`. It is either the root (`slug==code`, ticker==code, title==name) or a season series `<code>-YYYY` (ticker==slug, title==`<Name> YYYY`, scheduled year − season ∈ {0, 1}).
- Exactly 2 teams with `team.league == code`.
- Rejected: e-sports, and text matching `milb|minor league|ahl|echl|ncaa|college|u-?2[013]|reserve|academy` (`:25-28`).
- Postseason (World Series, Stanley Cup Final) passes because it stays in the same season series (`:132-137`, `tests/test_baseball_postseason_scope.py`).

### Whole-game moneyline identification

`strategy/filters.py:71-281`.

**Binary alignment:**
- Exactly 2 non-empty outcomes, 2 prices in [0, 1], 2 distinct token IDs.
- Soccer: labels are exactly `["Yes","No"]` and `negRisk == true` (a neg-risk result market).
- MLB/NHL/NFL: labels are team names, not Yes/No, and `negRisk == false` (`filters.py:97-109`).

**Market shape:**
- `sportsMarketType == "moneyline"`; `parentMarketId` empty.
- No `isFuture/future/isProp/prop/isAdvancement/advancement == true`.
- Rejected by regex on `groupItemTitle + question + slug` (`filters.py:18-32`):
  - series winner / World Series winner (explicit "World Series Game N" is kept);
  - draw-no-bet / `dnb`;
  - half/quarter/period/inning;
  - spread/handicap/total/over-under;
  - advance/qualify, penalties, corners, shots, goalscorer, TDs, runs, puck/run line, futures, season-long, championship/conference/division winner.
- The event is unique, has no `parentEventId`, and has exactly 2 teams with disjoint name/alias/abbreviation forms.

**Soccer draw handling** (`filters.py:120-188, 191-205, 269-281`):
- The market `description` must contain the exact clause "this market refers only to the outcome within the first 90 minutes of regular play plus stoppage time". Every clause that mentions extra time or penalties must explicitly *exclude* them; otherwise the market is rejected (`settlement_scope_*`).
- `groupItemTitle` is classified as:
  - `DRAW` if it is `draw`/`tie`, or `draw|tie <home> vs|v <away>`;
  - `HOME`/`AWAY` if it equals a team form;
  - otherwise rejected.
- Only the **YES token (index 0)** of each HOME/DRAW/AWAY market is tradable (`get_match_result_yes`, `filters.py:284-298`). **DRAW YES is eligible like any result** (`tests/test_scanner.py:170`). NO tokens are never traded, and `yes_only_mode` must be true (`config.py:695-698`).

**Direct sports:** both team tokens of the single moneyline condition are candidates, each mapped to HOME/AWAY (`filters.py:301-333`).

## 4. Data inputs per cycle

1. The Gamma keyset sweep above (event + nested markets). Market fields used:
   - `conditionId`, `outcomes`, `outcomePrices`, `clobTokenIds`, `negRisk`, `sportsMarketType`, `parentMarketId`;
   - future/prop flags, `groupItemTitle`, `question`, `slug`, `description`;
   - `active`, `closed`, `enableOrderBook`, `acceptingOrders`, `gameStartTime`, `liquidity(Num)`, `volume24hr`, `updatedAt`.

   Event fields used: `id`, `slug`, `parentEventId`, `active/closed/live/ended`, start fields, `sport{…}`, `tags`, `series`, `seriesSlug`, `teams[{name,alias,abbreviation,league}]`, `resolutionSource`. `score/elapsed/period` are archived but **not used for decisions**.
2. The CLOB order book (`get_order_book`) for every eligible winner token, walked for exact $5 (`scanner.py:175-182`; the walk is in the execution notes).
3. **No sports clock, game minute or tick0 is used.** The time variable is `in_play_hours = now − game_start` from Gamma start fields (`scanner.py:75-85, 136-153`). There is no "source minute" concept in this strategy.
4. For holdings, one of:
   - the full bid book for the signable shares (`get_sell_book_walks`, `bot.py:245-263`);
   - for a stop, independent Gamma `/markets?condition_ids=` (open, then `closed=true` fallback) + `/events/{id}` + CLOB market resolution status (`trader.py:2187-2260`, `gamma_client.py:404-465`).

## 5. Entry rules (exact)

### Snapshot pass

In `save_market_snapshots`, `scanner.py:155-324`, for each eligible token:
- The price is the **exact $5 baseline ask VWAP** = `5 / shares` from walking the asks until $5 is spent. If the full $5 depth is not available, there is no snapshot.
- If now is inside [experiment_start, entry_end) and `prob_min ≤ vwap ≤ prob_max` (±1e-9), claim an entry episode.
  - Frozen window: `2026-08-29T04:00:00Z` to `9999-12-31T23:59:59Z` (`config.py:25-32`).
  - The episode is unique per `token_id` in the runtime DB (`repository.py:1126-1190`).
  - Re-claim is allowed only if the previous state is proven no-POST: `BLOCKED_GUARD`, `PRE_SUBMISSION_CONTRACT_ERROR`, `QUEUED_NO_POST`, `NO_POST_RETRYABLE` (`repository.py:103-109`).
  - In effect, **the first in-band observation per token per arm**; there is no threshold-crossing requirement (`signals.py:49-60`).

### Candidate selection

`scan_buy_candidates`, `scanner.py:326-498`:
- The token is in the band and has a snapshot and a freshly claimed episode.
- **If more than one result token of the same event is in band → fail closed for that event** (`BLOCKED_GUARD`, retryable next cycle; `scanner.py:449-477`, `tests/test_scanner.py:193`).
- Candidates are sorted by `(in_play_hours, event_id, condition_id)`, so the earliest game-age goes first.

### Guards before any BUY

`bot.py:303-540`. All candidates are first marked `QUEUED_NO_POST`.

**Global blockers:**
- account guard error;
- blocking QUARANTINED;
- total reserved ≥ `max_positions`;
- open BUY fill/fee evidence gap;
- unknown-side reconciliation error;
- economic drawdown reached;
- economic evidence gap;
- league metadata drift.

The economic drawdown guard is: confirmed SELL P&L + proven resolution P&L since `economic_guard_start_utc` ≤ `−drawdown_loss_limit_usdc`, with **limit $300** frozen per `SportPolicy` (`config.py:196, 679-680`; `bot.py:320-334`). Guard start is per sport: soccer and nhl `2026-08-29T04:00Z`, mlb `2026-09-02T12:12Z`, nfl `2026-09-06T00:00Z` (`config.py:107-112`). AGENTS/STRATEGY text says "-$10", which is stale.

**Isolated (degraded but not blocking):** pending BUY/SELL, isolated stop-sell or pending-buy quarantines, BUY/SELL-side reconciliation gaps and errors.

**Per-cycle limit:** `min(max_new_positions_per_cycle=5, max_positions − reserved)` (`bot.py:535-540`). An attempt whose POST outcome is uncertain also consumes a slot (`bot.py:596-597`).

### Per-candidate execution

`Trader.execute_buy`, `trader.py:364-671`:
1. Account guard, shared across the Cat/Dog account's 3 DBs (`account.py:164-205`):
   - combined reserved slots < 20;
   - ledger BUY attempts in the rolling 60s < 5 (failed attempts count);
   - no recent balance/allowance failure;
   - SDK collateral balance − unresolved BUY principal ≥ amount.
2. `result_kind ∈ {HOME, DRAW, AWAY}`; current-run snapshot id present; `event_id` present.
3. Re-entry (`repository.py:224-330`, key `event_id × token_id`, cooldown `reentry_cooldown_hours=720`):
   - No holding on the condition, and no open position on the event (**one position per event**, `max_event_positions=1`).
   - The same token is blocked for 720h after close.
   - After a **confirmed** absolute-stop fill (`exit_reason` starts with `absolute_stop_confirmed_fill`, size > 0), **one** different token of the same event may be entered, on a later cycle.
   - After two distinct tokens in an event → `event_reversal_limit`.
   - A `SkippedMarket` within 720h blocks the condition.
4. Capacity is rechecked (DB + local untracked reservations), plus event capacity.
5. The frozen entry window and `0 ≤ in_play_hours ≤ hours_max` are revalidated at submit time (`trader.py:449-475`).
6. A fresh book is fetched and the adaptive notional is selected (`get_adaptive_buy_book_walk`, `api/clob_client.py:239-295`):
   - The baseline $5 walk must exist and its worst ask (limit) must be ≤ `prob_max`.
   - Then pick the **largest** of `{target} ∪ ladder ∩ [5, target]` whose full walk exists with worst ask ≤ `prob_max`.
   - Ladder: `5,10,15,20,25,30,40,50,75,100,150,200,250,500,750,1000` (`config.py:40-57`).
7. The **selected walk's** VWAP must still be within [prob_min, prob_max]. At the current $5 target this is identical to the baseline.
8. shares ≥ `min_order_size (5) + buffer (0)`; 0 < limit < 1.
9. FOK BUY at `amount = selected notional`, `limit_price = walk.limit_price` (worst ask needed), `max_limit_price = prob_max`. The trade is then `PENDING_BUY` until the exact fill is reconciled (see execution notes).
10. The recorded `stop_price_at_entry = _entry_stop_price(walk.vwap)` (`trader.py:645`).

## 6. Exit rules (exact)

Holdings are processed every cycle in `bot.py:218-301`, via `execute_sell` (`trader.py:1746-1902`).

**Book.** The full bid walk is for the SDK-signable shares, i.e. `buy_shares` floored to 0.01. A residual under 0.01 share is dust and is not sold (`trader.py:84-106`). A failed walk means no stop this cycle (see resolution below).

**Hold.** If `best_bid > effective_stop` → hold (`trader.py:1796`). The trigger is the **displayed best bid**, not the VWAP and not the midpoint.

**Stop path, when best_bid ≤ effective_stop:**
1. **OPEN proof** (`_stop_execution_is_explicitly_live`, `trader.py:2187-2260`):
   - Gamma: condition_id and event_id match; event `active & !closed & live & !ended`; market `active & !closed & enableOrderBook & acceptingOrders`.
   - **and** CLOB `get_market_resolution(condition).status == "OPEN"`.
   - If this fails, resolution handling runs instead. This suppresses selling into post-game 0.001 cleanup bids.
2. Per-cycle SELL budget: `max_emergency_sells_per_cycle = 1`. Further stops are deferred to the next cycle, but other holdings are still inspected (`trader.py:1840-1856`).
3. **Re-fetch** the full bid book. If `best_bid > stop` now, cancel (`trader.py:1861-1872`).
4. If the stop has been failing continuously for ≥ 180 min (`stop_sell_quarantine_timeout_minutes`) → QUARANTINED. No P&L is recorded and capacity stays reserved (`trader.py:1192-1235`).
5. Price safety (`_stop_execution_price_is_safe`, `trader.py:2262-2337`):
   - `executable_book`: bid, VWAP and limit in (0, 1); walk shares == signable shares; spread present and `≤ max_stop_spread = 0.10`.
   - `normal_envelope`: `vwap ≥ stop − 0.05` **and** `limit ≥ stop − 0.05` **and** projected loss `(buy_price − vwap)/buy_price ≤ 0.35`.
   - If the normal envelope fails but `executable_book` holds, a **gap stop** is still allowed (the PSG–Lille lesson). The only hard block is an incomplete or wide-spread book, which is recorded as a stop failure and retried.
6. FOK SELL of the **entire** signable holding at the worst bid needed (`_submit_full_holding_sell`, `trader.py:1904`, exit kind `absolute_stop`). There is no partial slicing. Zero fill → back to HOLDING. Mechanics are in the execution notes.

**Resolution / hold-to-resolution.** Resolution is applied only on paths where the book is unavailable or the stop lifecycle is not live (`_handle_midpoint_unavailable`, `trader.py:1097-1140`):
- Gamma proof (`filters.py:371-405`): `closed == true` with aligned final prices `[1,0]`/`[0,1]`, or `[0.5,0.5]` with `umaResolutionStatus=resolved` (void).
- Otherwise CLOB `status == RESOLVED`.
- Payout is booked only once the BUY fill and fee are exact (`trader.py:1057-1095`).

**Not used in the live config:** TP, trailing, forced time exit, partial exits.

**Dormant TP option.** `POLYBOT_TAKE_PROFIT_ENABLED=true`, allowed for Cat/Dog only (`config.py:245-249, 699-720`; `trader.py:2067-2185`):
- It forces the entry band to Cat 0.95 / Dog 0.97 – 0.989 and TP price 0.99 at $5, with book age ≤ 3s.
- The SELL must satisfy `0.99 ≤ limit ≤ vwap ≤ best_bid < 1` and `net_floor = limit·qty − buy_vwap·buy_size − buy_fee − sell_fee_quote − 0.001 > 0`.
- It is off by default and not in the 2026-09-26 contract.

## 7. Sizing and risk (frozen by validator)

| param | value | source |
|---|---|---|
| `buy_amount_usdc` target | $5 for all live policies (`SportPolicy.buy_amount_usdc`); validator allows 5–1000, cent precision | `config.py:197, 643-655` |
| baseline signal notional | $5 (constant) | `config.py:38` |
| max positions (per runtime DB) | 20; also account-wide combined 20 | `config.py:190, 666-670`; `account.py:180` |
| max per event | 1 | `config.py:191` |
| max new BUY per cycle | 5; account-wide BUY attempts 5 per rolling 60s | `config.py:192`; `account.py:182` |
| max emergency SELL per cycle | 1 | `config.py:193` |
| economic loss kill switch (new BUYs only) | −$300 | `config.py:196` |
| `experiment_capital_usdc` / `max_drawdown_stop` | 100 / 0.10 (validated, reporting only) | `config.py:194-195` |
| re-entry cooldown | 720 h | `config.py:535, 683` |
| DELAYED FOK zero-fill closure | 2 min | `config.py:537, 687` |
| stop SELL / ambiguous PENDING BUY → QUARANTINED | 180 / 180 min | `config.py:538-539, 689-692` |
| min order | 5 shares, 0 buffer | `config.py:540-541, 693` |
| per-cycle notional cap | `buy_amount × 5 ≤ $5000` | `config.py:671` |

## 8. Per-sport differences

| | soccer | mlb | nhl | nfl |
|---|---|---|---|---|
| tag | 100350 | 100381 | 899 | 450 |
| max in-play age | 4h | 8h | 5h | 6h |
| token | YES of HOME/DRAW/AWAY neg-risk markets (3 conditions/event) | both team tokens of 1 direct condition | same as mlb | same as mlb |
| regulation proof | 90'+stoppage clause required | n/a | n/a | n/a |
| stop floor | Cat 0.65 / Dog 0.60 | 0.70 | 0.70 | 0.70 |
| economic guard start | 2026-08-29T04:00Z | 2026-09-02T12:12Z | 2026-08-29T04:00Z | 2026-09-06T00:00Z |

## 9. Env / CLI / job-name overrides

- **CLI:**
  - `polybot run --job <runtime> --live` (real orders only with `--live`; `main.py:70-76`);
  - `polybot run-account --live --account polybot-cat|polybot-dog`;
  - `prepare-account`;
  - `status`;
  - `config`.
- **Job binding:** `--job` must be a registered `RUNTIME_SPECS` key. If the `JOB_NAME` env is set, it must equal the spec's Jenkins job (`config.py:813-821`). The simulation flag must match `spec.simulation_mode = False` (`config.py:640-642`). DB path: `data/<runtime_job>/trades.db` (`config.py:1058-1060`).
- **Child env:** `profile_environment` sets `POLYBOT_SPORT_FAMILY`, `BUY_AMOUNT`, `ENTRY_PROB_MIN/MAX`, `ENTRY_HOURS_MAX`, `ARCHIVE_HOURS_MAX`, `STOP_PRICE`, `MAX_ENTRY_DRAWDOWN`, `MAX_STOP_SLIPPAGE/SPREAD/LOSS_FRACTION` (`config.py:213-250`).
- **Other env** (precedence env > YAML > default): all `POLYBOT_*` keys in `load_config` (`config.py:838-1029`). The validator rejects any value that drifts from the frozen policy, so they are effectively read-only except:
  - `POLYBOT_LIFECYCLE_MODE` (`active|close_only|archive_only`);
  - `POLYBOT_CLOSE_ONLY_SPORT_FAMILIES`;
  - `POLYBOT_BUY_AMOUNT` (5–1000);
  - `POLYBOT_TAKE_PROFIT_*`;
  - `POLYMARKET_SIGNATURE_TYPE` (1|3).
- **Credentials** (values [REDACTED]): `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`.

## 10. Optimizer search space

"validator" means the value is enforced as frozen or bounded in code (`config.py:579-800`). A port that wants to optimize must lift the freeze, and each change needs a new preregistration. "suggested" means the range is not enforced in code.

| parameter | current | bounds | basis |
|---|---|---|---|
| `prob_min` per sport×arm | 0.91–0.99 | suggested [0.85, 0.99] | Historical arms were 0.91/0.92/0.94/0.96/0.99. Below ~0.85 is no longer a near-certain-winner hypothesis. Validator: `$5/prob_max ≥ 5 shares` (`config.py:753-755`). |
| `prob_max` | 0.999 | suggested [0.98, 0.999] | Must be < 1 (`clob_client.py:252`). The TP variant used 0.989. |
| `stop_price` floor | 0.60–0.70 | suggested [0.40, 0.85] | STRATEGY notes that every fixed stop was worse than holding to resolution in White data. A tighter 5pp stop was abandoned because it sold eventual winners. |
| `max_entry_drawdown` | 0.30 | suggested [0.05, 0.50] | Its relation to the floor decides which leg binds (§2). |
| `max_stop_slippage` / `max_stop_spread` / `max_stop_loss_fraction` | 0.05 / 0.10 / 0.35 | suggested spread [0.03, 0.20] | Only spread is a hard block; slippage and loss fraction are bypassed by the gap stop. |
| `hours_max` per sport | 4/8/5/6 | suggested ±50% | These approximate full game length; entries late in a game are the signal. |
| `buy_amount_usdc` | 5 | validator [5, 1000], cent precision | Scale-up needs depth evidence (STRATEGY "판정"). |
| `max_positions` / event / per cycle | 20/1/5 | suggested 1 per event fixed; others [5, 50] / [1, 10] | Keep 1 per event: results are mutually exclusive. |
| `min_liquidity`, `min_cumulative_volume` | 5000 | suggested [1000, 50000] | Server-side prefilter only; the $5 book is the real gate. |
| `reentry_cooldown_hours`, reversal count | 720 h, 1 reversal | suggested keep | This is a safety rule, not alpha. |

## 11. Open questions / unverified

- Which Jenkins jobs are actually enabled now is not verifiable from source. Bear/Tiger/Lion/Wolf may be retired or close-only (commit `15e43aa` mentions reducing to 10 active jobs). Check the Jenkins effective config and each DB's resolved config.
- AGENTS.md (soccer Cat 0.91), config.yaml (`prob_min 0.91`, `stop_price 0.70`) and STRATEGY/AGENTS "-$10 kill switch" are stale versus code (0.92, 0.65/0.60, $300). The code is authoritative.
- Resolution is applied only when the holding's book walk fails or the stop lifecycle is not OPEN. A resolved market that still shows a bid above the stop is not closed until the book disappears. Replicate this, or decide explicitly to poll resolution every cycle.
- `score/elapsed/period` are archived but unused. A port could add a game-clock feature, but that would be a new strategy variant, not a faithful port.
