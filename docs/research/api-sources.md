# Polymarket API sources for the research data-collection rebuild

Probed on 2026-09-30 (UTC) from the Mac mini (`/Volumes/t7/polylab/scratch/api-probe`). All probes were read-only public calls. No orders were placed and no keys were read.

Provenance tags:
- **[obs]**: seen in a real response.
- **[sdk]**: read from the installed `polymarket-client` 0.11.0 or `py-clob-client-v2` 1.2.0 source.
- **[docs]**: read from docs.polymarket.com (fetched 2026-09-30).

Fixtures live in `tests/fixtures/polymarket_api/`. They are trimmed, and wallet, name and pseudonym fields are replaced with placeholders.

## SDKs

| Item | Fact | Source |
|---|---|---|
| Official unified Python SDK | PyPI `polymarket-client` **0.11.0** (released 2026-09-23). Repo `github.com/Polymarket/py-sdk`. Import name is `polymarket`. Needs Python >= 3.11. Deps: httpx[http2], websockets <16, pydantic 2, eth-account | [obs] PyPI JSON |
| Not official | `polymarket`, `polymarket-py` (MattMaximo) and `polymarket-sdk` (counterfactual5) are third-party. `py-sdk` does not exist on PyPI. `polymarket-us` 2.1.0 is the Polymarket **US** SDK (a separate venue) | [obs] |
| CLOB v2 client | `py-clob-client-v2` **1.2.0**. Legacy `py-clob-client` 0.34.6 | [obs] |
| Relayer client | `py-builder-relayer-client` 0.0.2 | [obs] |
| `PublicClient` methods (all exist) | `list_price_history`, `list_trades`, `get_open_interests`, `get_event_live_volume`, `list_market_holders`, `get_resolutions`, `list_events`, `list_markets`, `get_sports`, `get_sports_market_types`, `get_order_book(s)`, `get_midpoint(s)`, `get_last_trade_price(s)` | [sdk] `polymarket/clients/public.py` |
| SDK to HTTP mapping | Data methods call **Data API v2** (`/v2/trades`, `/v2/activity`, `/v2/positions`, `/v2/value`, `/v2/holders`, `/v2/oi`, `/v2/live-volume`, `/v2/prices-history`, `/v2/resolutions`). **`list_price_history` uses Data API `/v2/prices-history`, not CLOB `/prices-history`.** Gamma lists use `/events/keyset` and `/markets/keyset`. CLOB market data uses `/book`, `/books`, `/midpoint`, `/price`, `/spread`, `/last-trade-price` | [sdk] `_internal/actions/{data,gamma,clob}.py` |
| SDK pagination | Data v2 uses a `cursor` param and a `pagination.next_cursor` response field. Gamma keyset uses an `after_cursor` param and a `next_cursor` response field. The SDK sends `limit` on every request | [sdk] `_internal/dispatch.py`, `request.py` |
| Stream specs | `MarketSpec(asset_ids, custom_feature_enabled)` connects to the CLOB market WS. `SportsSpec()` connects to the sports WS (no filter) | [sdk] `polymarket/streams/_specs.py` |
| Environment URLs | `clob_url=https://clob.polymarket.com`, `clob_market_ws_url=wss://ws-subscriptions-clob.polymarket.com/ws/market`, `clob_user_ws_url=.../ws/user`, `gamma_url=https://gamma-api.polymarket.com`, `data_url=https://data-api.polymarket.com`, `sports_ws_url=wss://sports-api.polymarket.com/ws`, `rtds_ws_url=wss://ws-live-data.polymarket.com`, `relayer_url=https://relayer-v2.polymarket.com` | [sdk] `polymarket/environments.py` |

## Feature to API table

| Feature | API | Endpoint | Verified fields | Limits | Notes |
|---|---|---|---|---|---|
| Sports list | Gamma | `GET /sports` | `id, sport, name, tags` (CSV of tag ids), `primaryTagId, series, ordering, resolution` | Cached (`cache-control: max-age=1200`) | [obs] `gamma_sports.json` |
| Sports market types | Gamma | `GET /sports/market-types` | `marketTypes[]`, for example `moneyline, spreads, totals, points, child_moneyline, both_teams_to_score, ...` | | [obs] |
| Events (games) | Gamma | `GET /events/keyset` | Event: `id, slug, title, gameId, startTime` (ISO, the game start), `eventDate, startDate` (listing creation), `endDate, closed, ended, live, score` (for example `"113-127"`), `period` (`"VFT"`), `elapsed, finishedTimestamp, seriesSlug, tags[{id,slug}], teams, markets[]` | **`limit` max 100** (500 and 1000 both return 100). Pages are about 150 KB per event with markets. `offset` is rejected, so use `after_cursor=<next_cursor>` | Filters in SDK source: `tag_id, closed, start_time_min/max` (game start), `start_date_min/max` (listing creation), `end_date_*`, `game_id, series_id, live, ended, event_date`. [obs][sdk] |
| Markets | Gamma | `GET /markets/keyset` | `conditionId, questionID, clobTokenIds` (JSON string), `outcomes` (JSON string), `outcomePrices` (JSON string), `sportsMarketType, line` (null for moneyline), `gameStartTime` (**not ISO**: `"2026-02-07 20:00:00+00"`), `closed, closedTime, umaResolutionStatus, umaResolutionStatuses, automaticallyResolved, resolvedBy, negRisk, orderPriceMinTickSize, feesEnabled, feeType, events[{id,...}]` | `limit` max 100. **Closed markets are excluded unless `closed=true`**: a `condition_ids=` lookup on a closed market returned empty without it | Filters: `tag_id, sports_market_types, condition_ids, clob_token_ids, game_id, start_date_*, end_date_*, uma_resolution_status`. [obs][sdk] |
| Legacy offset lists | Gamma | `GET /events?offset=` | | `offset=100000` returns 422 `"offset too large, use /events/keyset"` | [obs] |
| Price history (fine) | CLOB | `GET /prices-history?market=<token_id>&startTs&endTs&fidelity` | `{"history":[{"t":<unix s>,"p":<float>}]}` | Window **must be 15 days or less**. A 16-day window returns 400 `"'startTs' and 'endTs' interval is too long"`. No point cap: a 15-day `fid=1` window returned 21,595 points | **Keeps about 1-minute points for markets closed 7 months ago.** See the next section. [obs] |
| Price history (batch) | CLOB | `POST /batch-prices-history` with body `{markets:[token_ids up to 20], start_ts, end_ts, interval, fidelity}` | `{"history":{"<token>":[{t,p}]}}` | Same 15-day limit (20 days returns 400) | [obs][docs] `clob_batch_prices_history.json` |
| Price history (v2) | Data API | `GET /v2/prices-history?token_id&(start[,end] \| interval \| as_of)&bucket_seconds&cursor&limit` | `data[{timestamp, price, resolution_seconds}]`, `pagination{limit,offset,has_more,next_cursor}` | 15-day window (400 `"must span at most 15 days"`). `limit` max 10000. Minimum `bucket_seconds` is 60 (600 for `max/all/1m`, 300 for `1w`) | **Fine grains expire.** Docs give minimums of 1-minute data for 7 days, 5-minute for 60 days and 30-minute for 90 days. 3-hour and 12-hour data are kept permanently. For the Feb-2026 market, a 7-hour window came back **empty** even with `bucket_seconds` omitted, although the docs say the server coarsens instead. On a resolved market, `interval=max` ends with a settlement point (`price` 0 or 1, `resolution_seconds` 0). [obs][docs][sdk] |
| Trades (v1) | Data API | `GET /trades?market=<conditionId>&limit&offset&takerOnly&start&end&user` | `proxyWallet, side, asset, conditionId, size, price, timestamp, title, slug, eventSlug, outcome, outcomeIndex, transactionHash, name, pseudonym, ...` | `limit` of 5000 and 10000 accepted (10000 rows returned). **Max offset is 10000** (400 `"max historical trades offset of 10000 exceeded"`). `start`/`end` are honored together with `market` | **v1 retires on 2026-10-24** [docs]. `takerOnly` defaults to true: 4,326 rows versus 10,878 with `takerOnly=false` on the same market. [obs] |
| Trades (v2) | Data API | `GET /v2/trades?condition_id&user&event_id&side&taker_only&cursor&limit` | `proxy_wallet, side, token_id, condition_id, size, price, timestamp, outcome, outcome_index, transaction_hash, ...` (snake_case) | `limit` up to 1000 (1001 returns 400). No depth cap: all 10,878 rows were retrieved through the cursor | **`start`/`end` are ignored unless `user` is set.** A 1-hour window with `condition_id` returned all 4,326 rows. The SDK docstring says the same. `taker_only=true` gives 4,326 rows and `false` gives 10,878 (the extra rows are the maker side). Use `taker_only=true` for volume. [obs][sdk] |
| Activity | Data API | `/activity?user` (v1), `/v2/activity?user` | `type` (TRADE, SPLIT, MERGE, REDEEM, ...), `size, usdcSize/usdc_size, price, asset/token_id, side, outcomeIndex, transactionHash` | Rate limit 200 req/10s (v2) | [obs] |
| Positions | Data API | `/positions?user` (v1), `/v2/positions?user&status=OPEN\|REDEEMABLE\|REDEEMABLE_LOST\|MERGEABLE\|CLOSED` | v2: `token_id, condition_id, current_size, avg_price, entry_cost_usdc, realized_pnl, unrealized_pnl, current_price, status, redeemable, mergeable, opposite_token_id, negative_risk, ...` | | See the Redemption section. [obs][docs] |
| Portfolio value | Data API | `/value?user`, `/v2/value?user` | v1 `[{"user","value"}]`, v2 `{"data":{"proxy_wallet","value"}}` | | [obs] |
| Holders | Data API | `/holders?market`, `/v2/holders?condition_id&min_balance&include_pnl` | v2: `data[{token_id, holders[{proxy_wallet, amount, outcome_index, ...}]}]` | Page size up to 1000 (100 with `include_pnl`) [sdk] | [obs] |
| Open interest | Data API | `/oi?market`, `/v2/oi?condition_id` | `{"condition_id","value"}` (USDC) | Up to 20 condition ids per call [sdk] | [obs] |
| Live volume | Data API | `/live-volume?id=<eventId>`, `/v2/live-volume?event_id` | v2 `{"taker_volume_total", conditions[{condition_id, taker_volume}]}` | | [obs] |
| Resolution | Data API | `/v2/resolutions?condition_id \| event_id \| question_id` | `status, payouts` (for example `[0,1000000]`, in base units of 1e6), `resolution_source, resolved_at, resolved_block, price, proposed_price, was_disputed, extended_review` | Up to 20 ids | **Inconsistent rows.** The Feb-2026 row has **no `payouts`**, has `last_update_timestamp` as an epoch string and `reproposed_price:"69"` (unexplained). The Sep-2026 row has `payouts` and an ISO timestamp. [obs] |
| Order book | CLOB | `GET /book?token_id` | `market, asset_id, bids, asks, tick_size, min_order_size, neg_risk, last_trade_price, hash, timestamp` | 1500 req/10s | A closed market returns 404 `"No orderbook exists"`. [obs] |
| Live book, trades and BBO | WS | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | See the WebSocket section | | [obs] |
| Live scores | WS | `wss://sports-api.polymarket.com/ws` | See the WebSocket section | | [obs] |

## prices-history: key constraints

- **CLOB `/prices-history`** takes `market=<token_id>`. The docs describe `fidelity` as "accuracy in minutes, default 1" [docs], and it behaves that way [obs]: fid=5 gave 80 points in 7 hours and fid=60 gave 7 points.
- The CLOB window is limited to **15 days or less** [obs].
- On a market closed about 7.7 months ago (NBA Wizards vs Nets, 2026-02-07), `startTs/endTs` windows with `fidelity=1` returned 60-second median spacing. The same held for soccer on 2026-02-14, MLB on 2026-04-10 and NHL on 2026-03-05 (about 300 points per 5 hours) [obs].
- **`interval=max` or `1d` on that old market returns an empty `history`** [obs]. For a recent market, `interval=max` returned 10-minute points. For old markets, walk the history in explicit windows of 15 days or less.
- The meaning of `p` is not documented: the OpenAPI spec types it only as a float. Observed values fall on half-ticks (0.335, 0.475, 0.515). On a live token, the latest `p` was 0.515, which equalled `/midpoint` (0.515). `/last-trade-price` was 0.52 and the book BBO was 0.51/0.52. So **`p` appears to be the midpoint**, based on one observation [obs].
- **Data API `/v2/prices-history`** is the SDK path. It downsamples old data: for the old market, only 3-hour and 12-hour buckets remained and fine windows came back empty [obs][docs]. Use it for recent data (a 60-second grain was available for a 2026-09-26 game) and for settlement points. Use CLOB `/prices-history` for fine history on old markets.

## Enumerating closed sports games (verified)

The method below is verified [obs]:

```
GET https://gamma-api.polymarket.com/markets/keyset
    ?tag_id=<sport tag>&closed=true&sports_market_types=moneyline&limit=100
    [&start_date_min=<listing created after>]
    [&after_cursor=<next_cursor from the previous page>]
```

- Loop until `next_cursor` is absent. Rows come back in id order, not chronologically.
- Filter client-side on `gameStartTime` (a string like `"YYYY-MM-DD HH:MM:SS+00"`).
- The event key is `markets[].events[0].id`. The markets payload is much lighter than `/events/keyset`, which embeds every prop market (15 MB per 100 NBA events).
- Alternative: `/events/keyset?tag_id=..&closed=true&start_time_min=..&start_time_max=..` filters directly on game start time, but the payloads are heavy.

Closed moneyline games with a game start between 2026-02-01 and 2026-09-30 (probed 2026-09-30):

| Sport | tag_id | Moneyline markets | Games (events) | Notes |
|---|---|---|---|---|
| Soccer (all leagues) | 100350 | 46,248 | **15,143** | 3 Yes/No markets per game (home, draw, away: `"Will X win on <date>?"`). 463 pages, about 63 s |
| MLB | 100381 | 2,693 | **2,693** | |
| NHL | 899 | 552 | **552** | |
| NFL | 450 | 139 (all since Jan 10) | **99** | |
| NBA | 745 | 734 (all since Jan 10) | **619** | |

- The `start_date_min=2026-01-10` pre-filter did not change the result: NBA and NFL re-run without it gave 619 and 99.
- `umaResolutionStatus` was `resolved` for all but 2 soccer markets (`proposed`).
- The fixture is `closed_moneyline_counts.json`.

## Soccer league scope (probed 2026-10-01)

`collector/common.py::MAJOR_SOCCER_LEAGUES` is the one shared constant for collection (discover, backfill) and
the default analysis filter (`analysis/_common.py::league_filter_sql`, `--all-leagues` opts out). Other leagues
are no longer collected by default (`POLYLAB_SOCCER_MIN_VOLUME_OTHER` unset = excluded); rows already in
`core.db` are kept.

Median whole-game moneyline volume (3 Yes/No markets summed) of the latest 4 finished games per competition,
Gamma `/events/keyset?game_id=` [obs]:

| Code | Competition (Gamma `/series` title) | Median ML volume | In scope |
|---|---|---|---|
| `fifwc` | FIFA World Cup (series 11433) | 44.7M | yes |
| `epl` | Premier League | 1.79M | yes |
| `ucl` | UEFA Champions League | 1.53M | yes |
| `unl` | UEFA Nations League (series 11446) | 1.51M | yes |
| `lal` / `sea` / `bun` / `fl1` | La Liga / Serie A / Bundesliga / Ligue 1 | 0.68M / 0.60M / 0.45M / 0.36M | yes |
| `uel` | UEFA Europa League | 0.43M | yes |
| `mls` | MLS | 0.09M | yes |
| `euc` | European Championship (series 11430) | no 2026 games | yes (same tier as the World Cup) |
| `mex` / `lib` / `ere` / `fif` / `bra` / `arg` / `col` / `elc` / `afcq` / `por` / `tur` / `conl` | Liga MX / Libertadores / Eredivisie / **FIFA Friendly** (10238) / Brazil / Argentina / … / AFCON qual. / Portugal / Turkey / CONCACAF NL | 0.30M … 0.02M | no |

Qualifiers (`ewq`/`uef` = "UEF Qualifiers" 10243, `ueq` = "UEFA Euro Qualification" 11431), friendlies,
domestic cups and every other league are out of scope. The live strategies' `params.leagues`
(`epl, bun, fl1, lal, sea, mls, unl, ucl, uel`) is a subset of the major set.

## Game-level extra markets (probed 2026-10-01)

`GET /sports/market-types` lists ~170 values. Real soccer events (EPL Arsenal–Leeds 2026-10-10, EPL/La Liga/
MLS/UCL finished games) carry them in child events [obs]:

| Child event (slug suffix) | `sportsMarketType` values seen |
|---|---|
| main event | `moneyline` ×3 (`"Will X win on <date>?"`, `"Will X vs. Y end in a draw?"`) |
| `-more-markets` | `totals` (lines 0.5–8.5, question `"X vs. Y: O/U 2.5"`, outcomes `["Over","Under"]`), `spreads` (±1.5…5.5, outcomes = team names), `both_teams_to_score` (`"X vs. Y: Both Teams to Score"`, Yes/No), `soccer_team_totals` (`"X vs. Y: <Team> O/U 0.5"`, groupItemTitle `"<Team> O/U 0.5"`, Over/Under, lines 0.5–5.5 per team), `first_half_totals`, `second_half_totals`, `both_teams_to_score_first_half`/`_second_half`, `soccer_first_half_team_totals`, `soccer_second_half_team_totals` |
| `-halftime-result`, `-second-half-result`, `-exact-score`, `-first-to-score`, `-total-corners` | `soccer_halftime_result`, `soccer_second_half_result`, `soccer_exact_score`, `soccer_first_to_score`, corners |

- `soccer_home_team_totals`, `soccer_away_team_totals`, `team_totals_home`/`_away` are listed in
  `/sports/market-types` but were **not observed** on any soccer event; they are not mapped.
- Join to the game: the child event's **`parentEventId` = main event id (= `games.game_key`)**. It was present on
  all 19,430 closed soccer extra markets (2026 season, volume >= 10k) while `events[0].gameId` was **missing on
  10,049**. Of 5,676 staged major-league rows, 5,655 matched a stored game by `parentEventId` (the rest: game not
  stored), only 2,540 by `gameId`, and no row had a `gameId` match pointing at a different game. `gameId` is only
  a fallback. US sports lines sit in the main event (`events[0].id`).
- `/markets/keyset?game_id=` returned nothing (filter not honoured); `/events/keyset?game_id=` returns the main and
  child events of open **and** closed games (used post-game).
- Filters `sports_market_types=both_teams_to_score|soccer_team_totals|totals` with `volume_num_min` work on
  `/markets/keyset` [obs].

Collected mapping (`common.market_type_of` / `token_rows`, floors per market, current Gamma volume):

| Gamma | `markets.market_type` | `line` | token `side` | Scope / floor |
|---|---|---|---|---|
| `totals` | `total` | goals/points | `over` / `under` | soccer lines 0.5, 1.5, 2.5, 3.5 (`POLYLAB_SOCCER_TOTAL_LINES`), >= 10k (`POLYLAB_GOAL_MIN_VOLUME`); US any line >= 50k (`POLYLAB_LINE_MIN_VOLUME`) |
| `both_teams_to_score` | `btts` | NULL | `yes` / `no` | soccer, >= 10k |
| `soccer_team_totals` line 0.5 | `team_to_score` | 0.5 | Over = `home`/`away` (the scoring team, like the soccer moneyline Yes token), Under = `no` | soccer, >= 10k |
| `spreads` | `spread` | handicap | `home` / `away` (outcome team) | US only, >= 50k (soccer spreads dropped) |

Soccer extras are collected only for `MAJOR_SOCCER_LEAGUES`. Median FINAL per-market volume of the latest 4
finished games per competition [obs]: totals 2.5 — lal 213k, epl 135k, mls 113k, ucl 80k,
unl 130k, sea 70k, fl1 51k, bun 33k; totals 0.5 — epl 179k, bun 85k, others 2–25k; btts — ucl 97k, epl 43k,
lal 40k, unl 40k, sea 24k, fl1 18k, bun 17k, uel 15k, mls 5k; team O/U 0.5 (both teams together) — ucl 13k,
unl 7k, epl 6k, lal 5k, bun 3k, mls 2k, sea 1k. So at the 10k per-market floor **team_to_score is mostly
absent** except for big games (e.g. Fulham–Man Utd home O/U 0.5 = 10.7k); lower `POLYLAB_GOAL_MIN_VOLUME` to
widen it. Volumes grow in play: discover sees the current volume, `backfill --recent` re-checks the final
volume post-game, and `backfill --historical` phase C walks closed extras (own checkpoint
`backfill.hist.extras.enum.<sport>`, staging table `collector_hist_extras`). Extras store 1-minute prices for
outcome 0 only (Over / Yes / first team) in backfill; live poll/stream snapshot every token of an active game.

## NBA / NHL season start (probed 2026-09-30 23:00 UTC)

- NBA (tag 745): 12 open moneylines, all **preseason** games 2026-10-03…10-07 in series `nba-2026`, title
  `"Heat vs. Raptors"` (away first), outcomes = team nicknames. Regular-season games are not listed yet.
- NHL (tag 899): 102 open moneylines 2026-09-30…10-27 (preseason and regular season share series `nhl-2026`).
- A scratch discover with a 30-day horizon stored all 12 NBA and 193 NHL games (150 NHL games on/after 10-07,
  i.e. regular season); every moneyline token got the side
  implied by `teams[].ordering` and by the slug (`nba-mia-tor` → `tor` home) — including `Utah` (label) vs
  team name `Mammoth`.
- Open NBA/NHL totals/spreads were all below the 50k floor (none stored yet).

## WebSockets

### CLOB market channel: `wss://ws-subscriptions-clob.polymarket.com/ws/market`

- **Initial subscribe** [obs][sdk][docs]:
  ```
  {"type":"market","assets_ids":["<token>",...],"custom_feature_enabled":true}
  ```
  Optional fields [docs]: `initial_dump` (default true) and `level` (1, 2 or 3; default 2).
- **Dynamic updates** [sdk][docs]:
  ```
  {"operation":"subscribe"|"unsubscribe","assets_ids":[...],"custom_feature_enabled":bool}
  ```
- **Heartbeat**: the client sends the text `PING` every 10 s and the server replies `PONG`. The SDK treats the connection as stale after 30 s [sdk][obs].
- **Message types** have a flat shape and the key `event_type` [obs]:
  - `book`: `market, asset_id, timestamp` (ms string), `hash, bids[], asks[], tick_size, last_trade_price`.
  - `price_change`: `market, timestamp, price_changes[{asset_id, price, size, side, hash, best_bid, best_ask}]`. This also carries the **complement token**.
  - `last_trade_price`: `asset_id, price, size, side, fee_rate_bps, transaction_hash, timestamp`.
  - `best_bid_ask`: `asset_id, best_bid, best_ask, spread, timestamp`. Sent only with `custom_feature_enabled`.
  - `new_market`: a global broadcast, sent only with custom features on. It includes `sports_market_type, line, game_start_time, clob_token_ids`.
  - From SDK source only (not observed): `tick_size_change` (`old_tick_size, new_tick_size`) and `market_resolved` (`winning_asset_id, winning_outcome, asset_ids`; custom features only) [sdk].
- **Observed rate** over 60 s, for 2 assets (a live MLB pre-game moneyline plus a live cricket moneyline) [obs]:
  - 1836 `price_change` (about 30/s)
  - 19 `best_bid_ask`
  - 4 `book`
  - 2 `last_trade_price`
  - 16 `new_market`
  - 5 `PONG`
- Fixture: `ws_market_capture.json`.

### Sports channel: `wss://sports-api.polymarket.com/ws`

- **No subscribe frame.** The server pushes every active game [sdk][docs]. The docs say the server sends a text `ping` every 5 s and the client must reply `pong` within 10 s.
- **Observed: 0 text `ping` frames in 5 minutes** [obs]. Heartbeat may be at the WebSocket protocol level, which the websockets library answers automatically. The SDK marks the connection stale after 30 s without a text `ping`, which could cause reconnect churn. Verify this in production.
- **Frame fields** (bare JSON objects) [obs]:
  ```
  gameId, leagueAbbreviation, homeTeam, awayTeam, status, score, period, live, ended, elapsed
  ```
  `elapsed` appeared only for soccer. Examples:
  - `fif`: `score "0-1", period "2H", elapsed "81", status "InProgress"`
  - `cs2`: `score "000-000|1-0|Bo3", period "2/3"`
  - tennis: `score "3-6, 6-3, 4-2", period "S3"`
- **Cricket frames** carry `metadataGameId` and have **no `gameId` or `status`** (18 of 212 frames). The SDK `SportsGameResult` requires both, so the SDK stream **silently drops cricket frames** [obs][sdk].
- `sportradarGameId`, `finishedTimestamp`, `turn` and `slug` exist only in the SDK model. They were not observed [sdk].
- In 5 minutes: 212 frames across 25 games. Leagues seen: cs2, dota2, challenger, wta, fif, cricket.
- **No MLB, NBA, NHL, NFL or major soccer game was live during the capture**, so their payloads are unverified.
- **Join to Gamma:** `sports.gameId == Gamma event.gameId`. `/events/keyset?game_id=90123081` returned the main event plus its child events (Halftime Result, Exact Score, ...) with matching live `score`, `period` and `elapsed` [obs]. For NBA, `game_id=20023212` returned "Wizards vs. Nets" [obs].
- Fixtures: `ws_sports_capture.json` and `ws_sports_capture_5min.json`.

## Resolution: finding the winning outcome of a closed market

1. **Gamma** [obs]:
   - The market has `closed=true` and `umaResolutionStatus="resolved"`.
   - `outcomePrices` is `["0","1"]`, with index i aligned to `outcomes[i]` and `clobTokenIds[i]`.
   - `umaResolutionStatuses` can read `["proposed","proposed"]` even on a resolved market, so do not use it.
   - `automaticallyResolved` and `resolvedBy` are present.
2. **Data API `/v2/resolutions`** [obs]:
   - `payouts[i]` is in 1e6 base units. For the Sep-2026 game, `payouts` was `[0,1000000]`, which matched Gamma `outcomePrices` index 1.
   - Rows from about February 2026 have **no `payouts`**; only `price`/`proposed_price` are present. Fall back to Gamma in that case.
3. **Settlement point**: `/v2/prices-history?interval=max` ends with `price` 1.0 for the winning token and 0.0 for the loser, at `resolution_seconds` 0 [obs].
4. **WebSocket `market_resolved`** carries `winning_asset_id` (SDK model, not observed).
5. **Token alignment check** on 50 v1 trades: `asset == clobTokenIds[outcomeIndex]` and `outcome == outcomes[outcomeIndex]` held for every row [obs].

## Rate limits

Rate limits come from the docs, `api-reference/rate-limits`. They are Cloudflare IP-based, in requests per 10 s, and excess requests are throttled rather than rejected.

| Service | Limits |
|---|---|
| Gamma | General 4000; `/events` 500; `/markets` 300; `/markets`+`/events` listing 900 |
| Data v1 | General 1000; `/trades` 200; `/positions` 150 |
| Data v2 | General 800; `/v2/trades` 300; `/v2/positions` 200; `/v2/activity` 200; `/v2/prices-history` 200 |
| CLOB | General 9000; `/book` 1500; `/books` 500; `/midpoint` 1500; `/prices-history` 1000 |

- **No `X-RateLimit-*` headers were seen** in any response [obs]. The Data API exposes `retry-after` in `access-control-expose-headers` [obs].
- The SDK retries on 429 using `DATA_READ_RETRY` [sdk].

## Redemption (CTF redeemPositions) for proxy and deposit wallets

- **Official SDK support** [sdk]:
  - `SecureClient` and `AsyncSecureClient` provide `redeem_positions(condition_id=… | market_id=… | position_id=…)`, `merge_positions`, `merge_multiple_positions` and `split_position`.
  - For CTF markets, redeem builds `redeemPositions(collateral, 0x0, conditionId, [1,2])` against the market's adapter (collateral adapter or neg-risk adapter).
  - For v2 markets, it builds `redeem_v2_call` against `protocol_v2_router`, using the on-chain ERC1155 balance.
  - The docs say redemption goes through the CTF collateral adapter and **pays out in pUSD** [docs].
- **Wallet types** [sdk] `_internal/wallet.py`: `EOA`=0, `POLY_PROXY`=1, `GNOSIS_SAFE`=2, `DEPOSIT_WALLET`=3 (`POLY_1271` in `py_clob_client_v2` `SignatureTypeV2`).
- **Credentials:**
  - Non-EOA wallets go through the **gasless relayer** (`relayer-v2.polymarket.com`).
  - The source enforces: `"Gasless transactions require a Builder API Key or Relayer API Key. Pass api_key= ..."`. **The private key alone is not enough.**
  - `RelayerApiKey(key, address)`: create it in the UI under polymarket.com → Settings → API Keys → Relayer API Keys [docs]. It sends the headers `RELAYER_API_KEY` and `RELAYER_API_KEY_ADDRESS` [sdk].
  - `BuilderApiKey(key, secret, passphrase)`: create it in the UI under Settings → Builders, or with `SecureClient.create_builder_api_key()` [docs][sdk]. It sends `POLY_BUILDER_*` HMAC headers.
  - **Gas is sponsored by the relayer.**
  - **EOA** wallets broadcast directly through the RPC (`broadcast_eoa_call_sync`), so the EOA pays gas in POL [sdk].
- **Pitfall:** `SecureClient.create()` without `wallet=` resolves to, and **deploys**, the signer's default Deposit Wallet [sdk]. For an existing POLY_PROXY wallet, always pass `wallet=<proxy address>`.
- **Relayer REST** [docs]:
  - Submit a transaction (Builder or Relayer key auth).
  - Get a transaction by id.
  - Get the nonce.
  - Check whether a wallet is deployed.
  - The spec is `/api-spec/relayer-openapi.yaml`.
- **Position flags** [obs]:
  - Data API v1 `/positions` rows have `redeemable` and `mergeable`. However, **v1 `redeemable=true` includes losing positions** (`curPrice: 0`, sizes of 442k and 342k in the sample). A v1 filter would try to redeem losers.
  - v2 `/v2/positions` has `status` (`REDEEMABLE` for winners, `REDEEMABLE_LOST` for losers) plus `redeemable` and `mergeable`. The sample `status=REDEEMABLE` rows had `current_price: 1.0`, `redeemable: true` and `mergeable: true|false`.
  - Fixture: `data_v2_positions_redeemable.json`.

## Maker orders: GTC post-only, cancel, open orders (verified 2026-10-05)

Read from the installed `py-clob-client-v2` **1.2.0** source [sdk] and one supervised live order on account alias `lion`
(2026-10-05, Mac mini) [obs]. Used by `src/polylab/execution/{clob,maker}.py`.

| Item | Fact | Source |
|---|---|---|
| Limit order | `ClobClient.create_order(OrderArgs(token_id, price, size, side, expiration=0))` (`OrderArgs` = `OrderArgsV2`). `price_valid`: `tick <= price <= 1 - tick`, else `PolyException`. Rounding `ROUNDING_CONFIG[tick]`: size 2 dp; price dp by tick (0.01 → 2, 0.001 → 3). BUY: `takerAmount` = shares, `makerAmount` = shares × price (USDC); SELL the reverse | [sdk] `client.py`, `order_builder/builder.py` |
| Post | `post_order(order, order_type=OrderType.GTC, post_only=False, defer_exec=False)` → `POST /order` with body `{"order", "owner", "orderType", "deferExec", "postOnly"}`. **`post_only` with FOK/FAK raises `ValueError`** (client side). `create_and_post_order(args, options, order_type=GTC, post_only=False)` is the one-call form | [sdk] |
| Post-only GTC response | `{"success": true, "status": "live", "orderID": "0x…", "errorMsg": ""}` for a BUY far below the best bid | [obs] |
| Order state | `get_order(id)` → `GET /data/order/{id}`: `status` `LIVE` while resting, `CANCELED` after a cancel; `size_matched`, `original_size`, `price` (strings), `order_type` `GTC`, `associate_trades` | [obs][sdk] |
| Open orders | `get_open_orders(OpenOrderParams(id, market, asset_id), only_first_page=False)` → `GET /data/orders` (cursor pages until `LTE=`). Listing without a filter showed **only LIVE** orders; with `id=` the cancelled order was still returned 2 s after the cancel (so a filtered lookup is not proof of liveness — use `get_order` status) | [obs][sdk] |
| Cancel | `cancel_orders([id, …])` → `DELETE /orders`, response `{"canceled": [id], "not_canceled": {}}`; `get_order` showed `CANCELED` 2 s later. Also `cancel_order(OrderPayload(orderID))`, `cancel_all()`, `cancel_market_orders(OrderMarketCancelParams(market, asset_id))` — **polylab never uses the last two** (several variants may share an account) | [obs][sdk] |
| Tick size | `get_tick_size(token)` → `/tick-size` `minimum_tick_size`, or `get_clob_market_info` `mts`. TickSize ∈ {0.1, 0.01, 0.005, 0.0025, 0.001, 0.0001}. The probed soccer Over 0.5 market (best bid 0.97) had tick **0.001** | [sdk][obs] |
| Min order size | `/book` `min_order_size` (`"5"` shares on soccer O/U) and `get_clob_market_info` `mos` (5). polylab reads the `/book` value | [obs] |
| `get_clob_market_info(condition_id)` keys | `ao, aot, c, cbos, fd, gst, ibce, mbf, mos, mts, r, sd, t, tbf, v`. `fd` `{"r": 0.05, "e": 1, "to": true}` = taker-only fee rate 0.05 × p(1−p). `cbos: true` on the soccer O/U market — read as "clear book on start" (SDK dataclass `clear_book_on_start`): the venue clears resting orders at game start. Treated as a backstop only; polylab cancels its own entries 5 min before kickoff and resting TPs at kickoff | [obs][sdk] |
| Maker / taker role of a fill | Trade objects (`get_trades`) carry `taker_order_id`, `maker_orders[{order_id, matched_amount, price, …}]` and `trader_side` (`MAKER`/`TAKER`). polylab books a fill as maker only when its order id is in `maker_orders` (and `trader_side`, if present, agrees). Maker fee 0 follows from the taker-only schedule; an unknown schedule leaves the fill unbooked | [sdk][docs] |
| Rebates | Maker rebates are paid out-of-band (not on trade objects); not visible per fill, so not booked | [docs] |

Supervised verification (2026-10-05 05:03:56 UTC, created_at 1791176636): post-only GTC BUY 8.59 @ 0.582 (≈ 5.00 USDC)
on a LaLiga Over 0.5 token (`asset_id` 220694…288866, best bid 0.97 / ask 0.99, kickoff ≈ 131 h away) → `success`/`live`
→ `get_order` `LIVE`, `get_open_orders(id)` 1 row → `cancel_orders` `canceled: [id]` → `get_order` `CANCELED`,
`size_matched` 0. No fill, no position.

## Not available / caveats

- CLOB `/prices-history` has no tick-level (per-trade) series and no historical order-book depth. Old markets return 404 from `/book`. For fills, use Data API trades.
- CLOB `/prices-history` returns nothing for `interval=max` or `1d` on old markets.
- There is no historical sports play-by-play or score timeline over REST. Gamma events only hold the final `score`, `period` and `finishedTimestamp`. Live scores come only from the sports WebSocket, which has no replay.
- The sports WebSocket has no per-game or per-league filter; the client receives every active game.
- Data v2 `/v2/trades` has no time filter for market-level queries (`start`/`end` apply only with `user`). v1 honors them but retires on 2026-10-24.
- Data v2 prices-history loses fine grains after a few days or weeks (1-minute data for at least 7 days).
- No `X-RateLimit` headers are returned.
- The OpenAPI spec does not define the semantics of `p` in `/prices-history`; midpoint is inferred from one observation.
