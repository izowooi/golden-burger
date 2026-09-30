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
