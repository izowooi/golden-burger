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
                   "core_db_mb": 812.5, "books_db_mb": 120.0, "disk_free_gb": 900.1, "backfill_progress": {"games_done": 1200, "games_total": 5000}},
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

## Additive fields (beyond the examples above)
- overview `strategies[].pnl_mode`: `live|paper` — which ledger `pnl`/`trades`/`win_rate`/`roi` come from (paper variants report paper ledgers).
- strategies/<id>.json: `equity_mode`, `breakdown.by_day`, `stake_events[].from_mode/to_mode`, `open_positions[].status/mode`,
  breakdown rows may carry `win_rate`/`roi`.
- research.json: `event_by_score_state` (event sensitivity split by scorer's score state trailing|level|leading),
  `analysis_generated_at`, event rows also carry `n_isolated`, `mean_jump`, `mean_pre_price`, `reversion_1m`, `mean_peak_minute`.
- reports/index.json entries: `engine` (`claude|codex|null`), `generated_at`.

## reports
- `reports/index.json`: `[{"kind": "daily|weekly|monthly", "date": "2026-10-01", "slot": "morning|evening|dawn|null", "title": "...", "path": "reports/daily/2026-10-01-morning.md", "ai": true}]` newest first.
- `reports/<kind>/<name>.md`: Korean markdown report (deterministic tables + AI narrative + applied changes).
