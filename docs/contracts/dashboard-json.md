# Dashboard read-model contract (Supabase Storage bucket `polylab`, private)

Writer: `polylab publish` (Mac mini, every 5 min) and `polylab retro` (reports).
Reader: `dashboard/` server routes with `SUPABASE_URL` + `SUPABASE_SECRET_KEY` (never exposed to the browser).
Object API: `GET {SUPABASE_URL}/storage/v1/object/polylab/<path>` with `apikey` + `Authorization: Bearer` headers.
All times are ISO-8601 UTC strings (`...Z`); the dashboard renders KST. Money is USDC floats. Missing/unknown = `null`, never 0.

## latest/overview.json
```json
{
  "generated_at": "2026-10-01T00:00:00Z",
  "git_commit": "abc1234",
  "system": {
    "jobs": [{"name": "polylab-tick", "last_run_at": "...", "last_ok_at": "...", "status": "ok|failing|stale", "detail": "..."}],
    "collector": {"last_poll_at": "...", "live_games": 12, "tracked_markets": 40, "ws_last_message_at": "...",
                   "core_db_mb": 812.5, "books_db_mb": 120.0, "disk_free_gb": 900.1, "backfill_progress": {"games_done": 1200, "games_total": 5000},
                   "storage": {"projected_30d_gb": 21.4, "level": "ok|warn|critical|null", "budget_warn_gb": 50, "budget_crit_gb": 100,
                               "total_gb_now": 15.2, "steady_30d_gb": 5.7, "one_time_gb": 1.7, "naive_projected_30d_gb": 81.7,
                               "one_time_events": [{"area": "core", "day": "2026-10-05", "kind": "backfill", "note": "..."}],
                               "areas": {"raw": {"gb_now": 7.0, "gb_per_day": 0.03, "gb_30d": 0.8, "method": "raw_dated_dirs_daily_median|raw_dated_dirs_partial_day|samples_daily_median|samples_partial_day|naive_samples|naive_raw_dated_dirs|insufficient",
                                                 "one_time_gb": 0.0, "naive_gb_30d": 36.4, "steady_days": 1}}}},
    "ai": {"last_retro_at": "...", "last_retro_kind": "daily", "last_retro_ok": true, "proposals_applied_7d": 3}
  },
  "portfolio": {
    "total_equity_usdc": 1234.5, "total_cash_usdc": 800.0, "total_positions_value_usdc": 434.5,
    "accounts": [{"alias": "cat", "variant_id": "watermelon-cat", "cash_usdc": 100.0, "positions_value_usdc": 20.0,
                   "equity_usdc": 120.0, "redeemable_usdc": 0.0, "updated_at": "..."}]
  },
  "strategies": [{
    "id": "watermelon-cat", "family": "watermelon", "hypothesis": "...", "mode": "live|paper|off",
    "account": "cat", "sports": ["soccer"], "stake_usdc": 5, "next_stake_usdc": 10,
    "ladder": {"status": "hold|promote_ready|demote_warning", "trades_at_tier": 7, "needed": 20, "roi_ci_lo": -0.02},
    "params": {"prob_min": 0.92},
    "open_positions": 2, "open_cost_usdc": 10.0,
    "pnl": {"today": 0.4, "d7": 1.2, "d30": 3.4, "all": 5.6},
    "trades": {"all": 40, "wins": 37, "losses": 3}, "win_rate": 0.925, "roi": 0.03,
    "last_trade_at": "...", "last_change": {"at": "...", "summary": "prob_min 0.92→0.93 (autopilot)"}
  }],
  "alerts": [{"level": "warn|error", "at": "...", "message": "..."}]
}
```

- `system.collector.storage` (additive, 2026-10-05, `polylab health` → ops/storage.py): data growth budget. `areas` keys
  core, books, raw, ou05, general, strategies, manual, research; `gb_30d` = 7-day growth × 30 (raw from dated directories,
  the rest from hourly size samples; null until a day of samples exists). `level` warn > 50 GB/month, critical > 100.
  null when the measurement failed. 2026-10-06 (additive): `gb_per_day`/`gb_30d`/`projected_30d_gb`/`level` are the
  **steady state** = median daily rate of the last 7 days (today's partial day included, >= 6 h coverage), excluding days
  with a known one-time event of the area (backfills) and days up to the area's last collection config change (raw lean
  mode 2026-10-05); `steady_30d_gb` = `projected_30d_gb`; `one_time_gb` = growth of the excluded backfill days above the
  steady rate (reported separately, not projected); `naive_projected_30d_gb` / `areas.*.naive_gb_30d` = the old 7-day
  mean; `steady_days` = days behind the median (1 = provisional).


## latest/strategies/<id>.json
```json
{
  "id": "watermelon-cat", "generated_at": "...", "variant": {"...": "full yaml as json"},
  "param_history": [{"version": 3, "at": "...", "params": {}, "stake_usdc": 5, "mode": "live", "author": "autopilot", "rationale": "..."}],
  "stake_events": [{"at": "...", "from_usdc": 5, "to_usdc": 10, "reason": "...", "evidence": {}}],
  "equity_curve": [{"at": "...", "cum_pnl": 1.2}],
  "open_positions": [{"opened_at": "...", "sport": "soccer", "league": "EPL", "title": "Arsenal vs Chelsea", "outcome": "Arsenal",
                       "entry_price": 0.93, "shares": 5.37, "cost_usdc": 5.0, "mark_price": 0.95, "unrealized_pnl": 0.1, "game_minute": 71}],
  "recent_positions": [{"opened_at": "...", "closed_at": "...", "sport": "...", "title": "...", "outcome": "...", "entry_price": 0.93,
                         "exit_price": 1.0, "exit_reason": "resolution_win", "realized_pnl": 0.35, "stake_usdc": 5}],
  "breakdown": {"by_sport": [{"key": "soccer", "n": 10, "pnl": 1.0, "win_rate": 0.9}],
                 "by_entry_minute": [{"key": "60-75", "n": 5, "pnl": 0.3}],
                 "by_stake": [{"key": "5", "n": 30, "pnl": 2.0, "roi": 0.013}]}
}
```

## latest/research.json
```json
{
  "generated_at": "...",
  "dataset": {"games": 5000, "markets": 12000, "price_bars": 3400000, "from": "...", "to": "...", "by_sport": [{"sport": "soccer", "games": 2000}]},
  "calibration": [{"sport": "soccer", "phase": "all|pre|early|mid|late|final", "buckets": [
      {"p_lo": 0.9, "p_hi": 0.95, "n": 340, "mean_price": 0.925, "win_rate": 0.95, "ci_lo": 0.93, "ci_hi": 0.97, "gap": 0.025}]}],
  "brier": [{"sport": "soccer", "phase": "late", "n": 1000, "brier": 0.08}],
  "event_sensitivity": [{"sport": "soccer", "event": "goal", "minute_bucket": "75-90", "n": 120,
      "mean_abs_jump": 0.31, "median_jump": 0.28, "reversion_5m": -0.02, "reversion_10m": -0.03}],
  "stake_tiers": [{"tier_usdc": 5, "variants": 8, "trades": 300, "roi": 0.012, "pnl_std": 1.1, "max_drawdown": 9.0, "sharpe_like": 0.4}],
  "notes": ["..."]
}
```

## latest/transactions_24h.json
Every order/fill/resolution of every variant in the last 24h (newest first). Written by `polylab publish`;
the daily report's "지난 24시간 거래 내역" section is built from the same rows.
```json
[{"at": "2026-10-01T10:12:00Z", "variant_id": "watermelon-cat", "account": "cat", "mode": "live",
  "sport": "soccer", "league": "EPL", "game_title": "Arsenal vs Chelsea", "outcome": "Arsenal",
  "side": "BUY", "price": 0.93, "shares": 5.37, "usdc": 5.0, "fee_usdc": 0.0,
  "status": "CONFIRMED", "position_id": "...", "position_status": "open", "exit_reason": null, "realized_pnl": null}]
```
- `side`: `BUY` | `SELL` | `RESOLVE` (resolution payout of a position closed in the window; price = payout per share).
- `status`: fill status (`CONFIRMED`, `MATCHED`, `MINED`, `FAILED`, `PAPER`), `UNFILLED` (order failed/cancelled with no fill),
  `QUARANTINED` (position quarantined), `RESOLVED` / `UNSETTLED` for RESOLVE rows, else the upper-cased order status.
- `realized_pnl` is the position's realised P&L once settled (same value repeated on each row of that position); `null` = not settled.
- For unfilled orders `price` = limit price, `shares`/`usdc` = requested amounts, `fee_usdc` = null.

## latest/games_24h.json
Tracked games (soccer/MLB/NBA/NFL/NHL) that were live or finished in the last 24h, with how their win probabilities
moved. Written by `polylab publish` (and embedded as `games` in the daily report JSON); the daily report's
"지난 24시간 경기와 확률 움직임" section is rendered from the same object (`src/polylab/reports/games.py`).
```json
{"generated_at": "2026-10-01T00:00:00Z", "window": {"since": "2026-09-30T00:00:00Z", "until": "2026-10-01T00:00:00Z"},
 "scope": {"sports": ["soccer", "mlb", "nba", "nfl", "nhl"], "soccer_leagues": ["epl", "ucl"], "note": "..."},
 "notable_swing": 0.2, "excluded_out_of_scope": {"fif": 5},
 "summary_by_sport": [{"sport": "soccer", "games": 3, "resolved": 2, "favourites_resolved": 2, "favourite_win_rate": 0.5,
                        "favourite_avg_pre_price": 0.61, "avg_max_swing_10m": 0.18, "upsets": 1, "notable_swings": 1}],
 "games": [{"game_key": "1034902", "sport": "soccer", "league": "epl", "title": "Arsenal vs. Chelsea",
            "home_team": "Arsenal", "away_team": "Chelsea", "start_time": "...", "end_of_play": "...",
            "end_source": "ended_at", "status": "ended", "home_score": 2, "away_score": 1, "resolved": true,
            "result": "home", "volume_usd": 1250000.0, "traded": false, "favourite": "home",
            "favourite_pre_price": 0.55, "upset": false, "notable_swing": true, "max_swing_10m": 0.31,
            "outcomes": [{"side": "home", "label": "Arsenal", "token_id": "...", "condition_id": "0x...",
                          "volume_usd": 900000.0, "pre_price": 0.55, "min_price": 0.41, "max_price": 0.97,
                          "final_price": 0.995, "won": true, "in_play_bars": 110,
                          "swing_1m": {"delta": 0.22, "from_price": 0.5, "to_price": 0.72, "at": "...", "from_at": "...",
                                       "game_minute": 71.0, "period": "2H", "elapsed_min": 88.0, "sources": "poll_mid>poll_mid"},
                          "swing_10m": {"delta": 0.31, "from_price": 0.45, "to_price": 0.76, "at": "...", "from_at": "...",
                                        "game_minute": 72.0, "period": "2H", "elapsed_min": 89.0, "sources": "history>poll_mid"},
                          "path": [["2026-09-30T18:00:00Z", 0.55]]}],
            "events": [{"at": "...", "game_minute": 71.0, "minute_source": "feed", "scorer": "home",
                        "home_score": 1, "away_score": 0}]}]}
```
- Scope: all five sports; soccer only in the collector's league scope (`POLYLAB_SOCCER_LEAGUES`, default
  `MAJOR_SOCCER_LEAGUES`) plus any game a strategy traded in the window (`traded: true`). Out-of-scope soccer games
  are counted per league in `excluded_out_of_scope`. `games` is uncapped and sorted sport → league → kickoff.
- `outcomes`: `home`, `draw` (soccer), `away` — the whole-game moneyline token of that result (soccer: the "Yes" token
  of the team/draw market); price = 1-minute canonical price (poll_mid > ws_last > history) = implied probability.
- Play window = `[start_time, end_of_play)`; `end_source`: `ended_at` | `game_state` (first ended feed row) |
  `nominal` (finished, no end time: start + nominal duration) | `running` (still live at `until`).
  `min_price`/`max_price`/`swing_*` use in-play bars only, so the post-whistle drift to 0/1 never counts as a swing.
- `pre_price` = last bar that closed before kickoff (≤ 12h lookback); `final_price` = last bar before `resolved_at`
  (before `until` when unresolved). `won`/`result` come from market resolution only (`null` = unresolved).
- `swing_1m` = largest |Δ| between consecutive in-play bars ≤ 2 min apart; `swing_10m` = largest |Δ| between in-play
  bars ≤ 10 min apart; `delta` is signed (to − from), `at` = later bar. Moves that end converged (≤ 0.03 or ≥ 0.97)
  in the last 10 min of play are skipped (the result settling; `ended_at` can lag the whistle by a discover cycle).
  `game_minute` = feed clock at `at` (soccer clock extrapolated, stale clocks dropped → `null`; MLB = innings),
  `period` = feed period at `at` (`null` if older than 30 min); `elapsed_min` = wall-clock minutes since kickoff.
  `sources` shows which price sources the two endpoints came from (a history↔poll_mid pair can be a source artefact).
- `favourite` = outcome with the highest `pre_price`; `upset` = resolved game the favourite did not win (a soccer
  draw counts). `notable_swing` = `|max_swing_10m| ≥ notable_swing`.
- `path`: ≤ 120 `[iso, price]` points from 1h before kickoff to resolution (or `until`), last bar per equal time bin.
- `events`: single-side score increases from `game_states` (`analysis.events.detect_score_changes`);
  `minute_source` = `feed` | `wall_clock`.
- If core.db is missing the object carries `"error"` and an empty `games` list.

## latest/explore/<sport>.json
`<sport>` ∈ `soccer|mlb|nba|nfl|nhl`. Aggregates for the dashboard `/explore` (시각화) page, written by
`polylab analyze explore` / the hourly `publish_explore` step of `polylab publish` (`src/polylab/analysis/explore.py`,
cached under `<research_dir>/explore/`). Full DB, resolved games only; soccer = `MAJOR_SOCCER_LEAGUES`.
```json
{"sport": "nba", "generated_at": "2026-10-01T00:00:00Z",
 "scope": {"games": 594, "tokens": 1188, "bars": 248092, "from": "2026-02-01T00:00:00Z", "to": "2026-06-14T00:30:00Z",
           "leagues": null, "excluded_games": {"no_end": 23, "implausible_end": 5}},
 "min_n": 30, "heat_step": 0.05, "rel_step": 0.1,
 "time_cols": [{"key": "pre", "label": "경기 전", "lo": null, "hi": 0.0}, {"key": "t0", "label": "0–10%", "lo": 0.0, "hi": 0.1}],
 "phases": ["pre", "early", "mid", "late", "final"],
 "swing_edges": [0.0, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 1.01],
 "heatmap": [{"col": "t9", "b": 12, "p_lo": 0.6, "p_hi": 0.65, "n": 30, "games": 30, "mean_price": 0.6247,
              "win_rate": 0.8333, "gap": 0.2087, "ci_lo": 0.664, "ci_hi": 0.927}],
 "reliability": [{"phase": "late", "points": [{"p_lo": 0.6, "p_hi": 0.7, "n": 120, "games": 118, "mean_price": 0.648,
                  "win_rate": 0.7, "gap": 0.052, "ci_lo": 0.61, "ci_hi": 0.77}]}],
 "trajectory": {"grid": [-0.2, -0.18], "min_n": 30,
                "series": [{"key": "winner", "label": "최종 승자", "tokens": 594,
                            "points": [{"x": -0.2, "n": 590, "mean": 0.63, "p25": 0.5, "p50": 0.64, "p75": 0.77}]}]},
 "swings": [{"col": "t9", "window": "1m", "n": 21000, "counts": [9000, 3000, 2000, 2000, 1500, 1200, 900, 600, 400, 200, 150, 50],
             "p50": 0.005, "p90": 0.06, "p99": 0.18, "share_ge_5": 0.12, "share_ge_10": 0.05}],
 "notes": ["..."]}
```
- Time axis = normalised fraction f of regulation (`analysis._common.fraction_played`: game clock / regulation, else
  wall-clock elapsed / `WALL_MINUTES`, so breaks such as half-time sit inside a column). `time_cols`: `pre`, deciles
  `t0`…`t9`, `ot` (f ≥ 1 until `games.ended_at`). Bars after `ended_at` are dropped; games whose
  `ended_at − start_time` is missing or outside 0.6–2.5 × `WALL_MINUTES` are excluded (`scope.excluded_games`).
- `heatmap`: one row per (time col, 0.05 price bucket `b`); unit = one canonical price per outcome token per column
  (pre = last bar before start, else first bar in the column); outcome tokens = home/away/draw whole-game tokens;
  `gap = win_rate − mean_price` (> 0 under-priced); 95% Wilson CI. 2-way sports enter both sides, so `n` counts a
  game twice — `games` is the independent count. Cells with `n < min_n` should be shown as insufficient.
- `reliability`: same unit per phase (`pre|early|mid|late|final`, cuts 0.30/0.60/0.85) in 0.1 buckets.
- `trajectory`: decisive games (soccer draws excluded), home/away tokens, last price per 0.02 f bin on `grid`
  (−0.2…1.2), forward-filled ≤ 2 bins; series `winner|loser|fav_won|fav_lost|dog_won|dog_lost` (favourite = higher
  closing pre-game price). Points exist only where `n > 0`; late bins hold only longer games.
- `swings`: `|p(t) − p(t−1m)|` (`window` `1m`) and `|p(t) − p(t−10m)|` (`10m`) with exact gaps on each token's
  dominant price source, bucketed by the later bar's column; `counts[i]` = changes in `[swing_edges[i], swing_edges[i+1])`.

## latest/explore/games_index.json
Games that started in the last 7 days (all soccer leagues the collector stored), newest first.
```json
{"generated_at": "2026-10-01T00:00:00Z", "window_days": 7, "max_points": 300,
 "games": [{"game_key": "1097833", "sport": "mlb", "league": "mlb", "title": "Chicago White Sox vs. Houston Astros",
            "start_time": "2026-09-30T21:00:00Z", "ended_at": null, "status": "live", "home_score": 3, "away_score": 4,
            "outcomes": 2, "score_events": 6, "positions": 1}]}
```

## latest/explore/games/<game_key>.json
One per index entry (`game_key` matches `^[A-Za-z0-9_-]{1,64}$`). No `generated_at`, so bytes change only when the
game does and publish uploads only changed games.
```json
{"game_key": "1097833", "sport": "mlb", "league": "mlb", "title": "Chicago White Sox vs. Houston Astros",
 "home_team": "Chicago White Sox", "away_team": "Houston Astros", "start_time": "2026-09-30T21:00:00Z",
 "ended_at": null, "status": "live", "home_score": 3, "away_score": 4,
 "outcomes": [{"side": "home", "label": "Chicago White Sox", "token_id": "123", "won": null,
               "points": [["2026-09-30T19:00:00Z", 0.45]]}],
 "score_events": [{"at": "2026-09-30T21:20:00Z", "scorer": "away", "points": 1, "home_score": 0, "away_score": 1,
                   "game_minute": 1.5}],
 "positions": [{"variant_id": "cherry-tiger", "mode": "live", "side": "home", "outcome": "Chicago White Sox",
                "status": "closed", "opened_at": "2026-09-30T22:00:00Z", "entry_price": 0.93, "shares": 5.3,
                "stake_usdc": 5, "closed_at": "2026-09-30T22:30:00Z", "exit_price": 0.97,
                "exit_reason": "take_profit", "realized_pnl": 0.2}]}
```
- `points`: canonical 1-minute prices from 2h before start to `ended_at` (running games: now), min/max-per-bucket
  downsampled to ≤ `max_points` per outcome. `score_events` = `analysis.events.detect_score_changes` on `game_states`
  (sports WS, only since 2026-09-30). `positions` = every strategy ledger position on the game with an entry price.

## latest/attention.json
The retro's attention inbox (what the thesis owner should know or decide), copied from the committed
`reports/attention.json` by `polylab publish` (`snapshot.attention_snapshot`). Human view: `reports/attention.md`
on GitHub (`url`). Empty lists before the first retro that writes the inbox.
```json
{"generated_at": "2026-10-01T00:00:00Z",
 "url": "https://github.com/izowooi/golden-burger/blob/main/reports/attention.md",
 "open": [{"id": "dead_variant:apricot-eco", "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z",
           "severity": "decide", "category": "decision_needed", "title": "apricot-eco 3일 이상 진입 0건 (대상 경기 41개 있었음)",
           "detail": "...", "evidence_ref": "strategies/apricot-eco.yaml", "source": "rule", "status": "open",
           "resolved_at": null, "resolution": null}],
 "resolved": [{"id": "ai_failed", "created_at": "2026-09-30T18:30:00Z", "updated_at": "2026-09-30T18:30:00Z",
               "severity": "warn", "category": "risk", "title": "AI 회고 실패: 결정론 리포트만 생성", "detail": "...",
               "evidence_ref": "reports/daily/2026-10-01-dawn.md", "source": "rule", "status": "resolved",
               "resolved_at": "2026-09-30T23:00:00Z", "resolution": "조건 해소 (자동)"}]}
```
- `open` is sorted most severe first (`critical > warn > decide > info`), newest `updated_at` first within a severity;
  `resolved` holds items resolved in the last 14 days, newest first.
- `source`: `rule` (deterministic, auto-resolves when its condition clears) | `ai` (retro engine, id prefix `ai:`, never
  `critical`, expires 7 days after it was last emitted). `category`: `decision_needed|risk|data_quality|research_finding|system_change|question`.
- `evidence_ref`: comma-separated repo paths (rule) or AI context-pack paths such as `metrics/<id>.json` (public copy:
  `reports/context/latest/`). Text is scrubbed and uses account aliases only.

## latest/manual.json
Track 2: the thesis owner's manual bets, recorded watch-only from public wallet addresses (`~/.polylab/watch.env`,
no key) by `polylab manual sync` into `<data>/manual/<alias>.db`; built by `src/polylab/manual/report.py`
(`snapshot.manual_snapshot`, same object as the daily report's "수동 AI 베팅 (트랙 2)" section). Accounts appear by
label/alias only; wallet addresses are never stored in the ledgers and are on the publish secret list.
```json
{"generated_at": "2026-10-01T00:00:00Z",
 "accounts": [{"account": "트랙2-A", "since": "2026-09-28T00:00:00Z", "last_sync_at": "...", "bankroll_usdc": 1000.0,
               "bankroll_first_seen_at": "...", "all_realized_pnl": 41.9, "drawdown_pct": 0.042,
               "credits_usdc": {"MAKER_REBATE": 0.5},
               "track2": {"realized_pnl": {"today": 0.0, "d7": 41.9, "d30": 41.9, "all": 41.9}, "settled": 2, "wins": 2,
                          "losses": 0, "sold": 0, "win_rate": 1.0, "roi": 0.033, "cost_settled": 1263.9, "open": 1,
                          "open_cost_usdc": 100.4, "unrealized_pnl": 1.2, "unrealized_unknown": 0, "fees_usdc": 3.1,
                          "fees_unknown": 0}}],
 "totals": {"realized_pnl": {"today": 0.0, "d7": 41.9, "d30": 41.9, "all": 41.9}, "settled": 2, "wins": 2, "losses": 0,
            "sold": 0, "win_rate": 1.0, "roi": 0.033, "cost_settled": 1263.9, "open": 1, "open_cost_usdc": 100.4,
            "unrealized_pnl": 1.2, "unrealized_unknown": 0, "fees_usdc": 3.1, "fees_unknown": 0},
 "by_stake": [{"band": "600+", "n": 1, "pnl": 38.1, "cost": 761.9, "roi": 0.05, "wins": 1, "losses": 0, "win_rate": 1.0}],
 "trades_24h": [{"at": "...", "account": "트랙2-A", "position_id": "wolf:3f2a9c01d4", "sport": "soccer", "league": "unl",
                 "game": "Greece vs. Netherlands", "kickoff": "...", "market": "O/U 0.5 Over", "track2": true,
                 "side": "BUY", "price": 0.95, "shares": 800.0, "usdc": 761.9, "fee_usdc": 1.9,
                 "result": "redeemed", "realized_pnl": 38.1}],
 "open_positions": [{"account": "트랙2-A", "position_id": "...", "sport": "soccer", "league": "fif", "game": "Wales vs. Iceland",
                     "kickoff": "...", "market": "O/U 0.5 Over", "market_type": "total", "line": 0.5, "side": "over",
                     "outcome": "Over", "track2": true, "opened_at": "...", "closed_at": null, "entry_price": 0.92,
                     "shares": 100.0, "stake_usdc": 92.37, "entry_fee_usdc": 0.37, "result": "open",
                     "proceeds_usdc": null, "realized_pnl": null, "mark_price": 0.95, "unrealized_pnl": 2.63,
                     "implied_p00_at_entry": 0.08, "link_source": "gamma", "quarantine_reason": null}],
 "settled_24h": [{"account": "트랙2-A", "game": "Greece vs. Netherlands", "result": "redeemed", "realized_pnl": 38.1}],
 "other": {"settled": 2, "realized_pnl": 1.12, "open": 0},
 "quarantined": 0}
```
- One position per (account, outcome token). Money: `stake_usdc` = actual USDC spent (BUY `usdc_size`, taker fee
  included); `proceeds_usdc` = SELL cash (net of fee) + REDEEM cash or remaining shares × confirmed payout;
  `realized_pnl` = proceeds − stake only when fully settled, else `null`. `fee_usdc` = `|size×price − usdc_size|`,
  `null` when implausible (unknown, never 0). `unrealized_pnl` uses the Data API `/v2/positions` mark, shown separately.
- `result`: `open | closed_sell | resolved_win | resolved_loss | resolved_split | redeemed | quarantined`. Win rate =
  (`resolved_win`+`redeemed`) / (+`resolved_loss`); `sold` = closed by sale before resolution.
- `track2: true` = linked to a sports game (core.db, else Gamma market lookup); `totals`/`by_stake` cover Track 2 only;
  non-game positions are summarised in `other`. `quarantined` (SPLIT/MERGE/CONVERSION or fills outside the
  `SINCE` window) are excluded from money and counted.
- `implied_p00_at_entry` = market P(0-0) for total-goals 0.5 bets (1 − Over entry price, or the Under price).
- `bankroll_usdc` = optional `WATCH_<ALIAS>__BANKROLL_USDC`, frozen when first seen; `null` disables the −10%
  attention rule (`drawdown_pct` = all realised / bankroll). `trades_24h` also carries `RESOLVE` rows (price = payout/share).
- `trades_24h`/`settled_24h` hold the publish window (last 24h). Without any ledger every list is empty and `totals` is `null`.

Additional keys (2026-10-04): `positions` — every recorded position of every watched account (same row shape as
`open_positions`, incl. `result`, `realized_pnl`, `closed_at`, `track2`, `implied_p00_at_entry`), newest `opened_at`
first; `predictions` — all-time `manual/predictions` join (`{rows, unmatched, predictions}` as in the weekly report) or `null`.

## latest/ou05/summary.json
Soccer O/U 0.5 market-life study (`docs/research/ou05-overround-study.md`), computed by `polylab analyze ou05`
(`src/polylab/analysis/ou05.py`, end of the hourly `polylab-ou05-discover` job) into `<research_dir>/ou05/latest/`;
`polylab publish` (`publish_ou05`) only uploads changed files from there and never computes. Source = `data/ou05/` only
(all soccer leagues).
```json
{"generated_at": "2026-10-05T02:00:00Z",
 "scope": {"markets": 1400, "open": 932, "resolved": 464, "poll_rows": 2053, "history_rows": 781964,
           "from": "2026-07-03T17:18:00Z", "to": "2026-10-05T01:37:00Z"},
 "bin": 0.005, "heartbeat_s": 600,
 "bands": [{"key": "d30p", "label": "30일+ 전", "kind": "pre"}, {"key": "ip0", "label": "킥오프 0–15′", "kind": "inplay"}],
 "tiers": [{"key": "all", "label": "전체"}, {"key": "major", "label": "주요 리그"}, {"key": "vol_ge_10k", "label": "거래량 ≥ 1만"}],
 "overround": [{"band": "h1", "tier": "major", "markets": 8, "minutes": 83, "rows": 120, "two_sided_share": 1.0,
                "sum_ask": {"p10": 1.01, "p25": 1.01, "p50": 1.02, "p75": 1.02, "p90": 1.02},
                "sum_bid": {"p10": 0.98, "p25": 0.98, "p50": 0.98, "p75": 0.99, "p90": 0.99},
                "spread": {"p10": 0.01, "p25": 0.01, "p50": 0.02, "p75": 0.02, "p90": 0.02}}],
 "distribution": {"edges": [1.0, 1.01, 2.0], "series": [{"tier": "all", "phase": "pre|last24h|inplay", "markets": 900,
                  "minutes": 9000, "shares": [0.0, 0.3, 0.1]}]},
 "leagues": [{"league": "epl", "tier": "major", "markets": 15, "volume_median": 0.0, "minutes": 165,
              "pre": {"p25": 1.01, "p50": 1.02, "p75": 1.12}, "last1h": null, "inplay": null}],
 "volume_relation": {"basis": "...", "markets": 30, "spearman_volume": -0.39, "spearman_liquidity": -0.5,
                     "rows": [{"metric": "volume|liquidity", "lo": 0, "hi": 100, "markets": 27, "p25": 1.02, "p50": 1.02, "p75": 1.03}]},
 "calibration": [{"band": "h1", "tier": "all|major|other", "n": 54, "n_poll": 0, "mean_over": 0.93, "over_rate": 0.85,
                  "ci_lo": 0.73, "ci_hi": 0.92, "gap": -0.08, "nil_rate": 0.15}],
 "quality": {"ou05_poll_gap": {"events_7d": 1, "last_at": "..."}},
 "notes": ["..."]}
```
- `sum_ask = over_ask + under_ask` (level 1), `sum_bid = over_bid + under_bid`, `spread = over_ask − over_bid`. The two books
  are mirrors (`under_ask = 1 − over_bid`), so `sum_ask − 1 = spread` and `sum_bid = 1 − spread`.
- Quantiles are **time-weighted** (row weight = seconds to the market's next row capped at `heartbeat_s`; 60 when the next
  row is more than `heartbeat_s` + 180 s later) over
  poll rows only, from `bin`-wide histograms. Quantile objects are `null` when a band has no two-sided quote.
- `bands`: pre-kickoff (`d30p,d14,d7,d3,d1,h6,h1,m15,m0`) then wall-clock minutes since kickoff
  (`ip0…ip120`, `post` ≥ 180′), from the registry's latest kickoff. `tiers`: `all|major|other|vol_ge_10k|vol_1k_10k|vol_lt_1k`.
- `distribution.series[].shares[i]`: time share in bucket i of `[<edges[0]], [edges[0],edges[1]) … [≥edges[-1]]`
  (length `len(edges)+1`).
- `calibration`: resolved markets, one observation per (market, band) = time-weighted Over price (poll mid when the Over
  spread ≤ 0.10, else history mid; a history mid of exactly 0.5 = empty-book placeholder, dropped); `gap = over_rate − mean_over` (> 0 Over under-priced); `nil_rate` = 0:0 share.

## latest/ou05/markets_index.json
```json
{"generated_at": "...", "max_points": 400,
 "markets": [{"condition_id": "0x…64 hex", "league": "epl", "title": "Leeds United FC vs Newcastle United FC", "major": true,
              "created_at": "...", "game_start": "...", "closed_at": "...", "closed": true, "resolved_over": true,
              "final_score": "2-1", "volume": 53075.6, "liquidity": 1200.0, "rows": 2900, "poll_rows": 0,
              "from": "...", "to": "..."}]}
```
At most 120 markets with stored rows: open markets kicking off within −6 h…+7 d (top 50 by volume) plus markets that
kicked off in the last 30 days (resolved first, by volume); newest kickoff first.

## latest/ou05/markets/<condition_id>.json
`condition_id` matches `^0x[0-9a-f]{64}$`. No `generated_at`, so unchanged markets are not re-uploaded.
```json
{"condition_id": "0x…", "league": "epl", "title": "...", "major": true, "created_at": "...", "game_start": "...",
 "closed_at": null, "closed": false, "resolved_over": null, "final_score": null, "volume": 12000.0, "liquidity": 3000.0,
 "goals": [{"at": "...", "scorer": "home", "home_score": 1, "away_score": 0}],
 "columns": ["at", "over_bid", "over_ask", "under_bid", "under_ask", "sum_ask_max", "over_mid", "src"],
 "points": [["2026-10-05T01:00:00Z", 0.93, 0.99, 0.01, 0.07, 1.06, 0.96, "p"]]}
```
- `points`: the whole stored life (history + poll), ≤ `max_points` equal time bins, last row per bin; `sum_ask_max` = the
  bin's largest sum_ask. `src` `p` = poll row (bid/ask known), `h` = history row (only `over_mid`; others `null`).
- `goals`: score increases from core.db `game_states` (only games the main collector tracks, since 2026-09-30); else `[]`.

## Additive fields (beyond the examples above)
- overview `strategies[].pnl_mode`: `live|paper` — which ledger `pnl`/`trades`/`win_rate`/`roi` come from (paper variants report paper ledgers).
- overview `strategies[].per_sport` (bool) and `strategies[].sports_detail[]` (2026-10-05, owner decision `sports3:per-sport`):
  `{"sport": "nba", "mode": "live|paper|off", "stake_usdc": 5, "trades": 12, "pnl": 0.4, "roi": 0.01,
  "next_stake_usdc": null, "ladder": {"status", "trades_at_tier", "needed", "roi_ci_lo"} | null}` — one row per sport of the
  variant (effective mode = stricter of the yaml master `mode` and the sport's own). `ladder` is per (variant, sport) for
  per-sport variants and `null` for legacy list-form variants (their ladder is the variant-level `ladder`). For per-sport
  variants the top-level `mode` is the most permissive sport mode and `stake_usdc`/`ladder` are those of the first live sport.
- overview `strategies[].order_style` and `strategies[].execution` (2026-10-05, owner decision `fees:maker-preferred`):
  `order_style` = `{"<sport>|all": "taker|maker"}` from params (+ `sport_overrides`). `execution` = `null` (never rested an
  order) or per mode `{"live"|"paper": {"entries", "entries_active", "entries_filled", "fill_rate" (filled / finished maker
  entries, null if none finished), "avg_wait_min" (first post -> first fill), "maker_fills", "taker_role_fills",
  "maker_fee_usdc", "taker_fee_est_usdc", "fees_saved_usdc" (taker estimate − actual, venue-reported maker fills only),
  "fee_unknown_fills", "tp_orders", "tp_filled", "tp_active"}}`. Paper numbers come from the conservative trade-through
  simulation (execution/maker.py).
- strategies/<id>.json `stake_events[].sport`: the sport a stake/mode move applied to (`null` = whole variant).
- strategies/<id>.json: `equity_mode`, `breakdown.by_day`, `stake_events[].from_mode/to_mode`, `open_positions[].status/mode`,
  breakdown rows may carry `win_rate`/`roi`.
- research.json: `event_by_score_state` (event sensitivity split by scorer's score state trailing|level|leading),
  `analysis_generated_at`, event rows also carry `n_isolated`, `mean_jump`, `mean_pre_price`, `reversion_1m`, `mean_peak_minute`.
- reports/index.json entries: `engine` (`claude|codex|null`), `generated_at`.

## reports
- `reports/index.json`: `[{"kind": "daily|weekly|monthly", "date": "2026-10-01", "slot": "morning|evening|dawn|null", "title": "...", "path": "reports/daily/2026-10-01-morning.md", "ai": true}]` newest first.
- `reports/<kind>/<name>.md`: Korean markdown report (deterministic tables + AI narrative + applied changes).
