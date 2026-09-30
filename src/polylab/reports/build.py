"""Deterministic report data: one dict that feeds the markdown report, Slack, the dashboard
snapshot and the AI context pack. No AI, no network except optional Jenkins health.

Money rules (ARCHITECTURE §1.4): realised PnL = settled positions only (see analysis.performance);
mark-to-market of open positions is shown separately and never added to realised numbers.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pandas as pd

from polylab import registry, settings
from polylab.analysis import _common as C
from polylab.analysis import integrations, performance
from polylab.ops import health as health_mod

SLOTS = {"dawn": (3, 30), "morning": (8, 0), "evening": (19, 30)}
SLOT_KO = {"dawn": "새벽", "morning": "아침", "evening": "저녁"}
KIND_KO = {"daily": "일일", "weekly": "주간", "monthly": "월간"}


# ------------------------------------------------------------------ time helpers

def kst(ts: int | None, fmt: str = "%m-%d %H:%M") -> str:
    if ts is None:
        return "–"
    return dt.datetime.fromtimestamp(int(ts), C.KST).strftime(fmt)


def infer_slot(now: int) -> str:
    hour = dt.datetime.fromtimestamp(now, C.KST).hour
    return "dawn" if hour < 6 else ("morning" if hour < 14 else "evening")


def window(kind: str, now: int) -> tuple[int, int]:
    if kind == "daily":
        return now - 86400, now
    if kind == "weekly":
        return now - 7 * 86400, now
    local = dt.datetime.fromtimestamp(now, C.KST)
    this_month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month = (this_month - dt.timedelta(days=1)).replace(day=1)
    return int(prev_month.timestamp()), int(this_month.timestamp())


def report_name(kind: str, now: int, slot: str | None) -> tuple[str, str]:
    """(file stem, KST date string)."""
    local = dt.datetime.fromtimestamp(now, C.KST)
    date = local.strftime("%Y-%m-%d")
    if kind == "daily":
        return f"{date}-{slot}", date
    if kind == "weekly":
        y, w, _ = (local - dt.timedelta(days=1)).isocalendar()
        return f"{y}-W{w:02d}", date
    lo, _ = window("monthly", now)
    return dt.datetime.fromtimestamp(lo, C.KST).strftime("%Y-%m"), date


def title_for(kind: str, now: int, slot: str | None) -> str:
    stem, date = report_name(kind, now, slot)
    if kind == "daily":
        h, m = SLOTS[slot]
        return f"polylab 일일 리포트 {date} {SLOT_KO[slot]} ({h:02d}:{m:02d} KST)"
    if kind == "weekly":
        return f"polylab 주간 리포트 {stem}"
    return f"polylab 월간 리포트 {stem}"


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=settings.REPO_ROOT, capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# ------------------------------------------------------------------ core lookups

class CoreLookup:
    """Titles, outcomes and latest marks from core.db (tolerates a missing DB)."""

    def __init__(self, paths):
        self.conn = C.open_ro(paths.core_db)
        self._cond: dict[str, dict] = {}
        self._games: dict[str, dict] = {}

    def close(self):
        if self.conn is not None:
            self.conn.close()

    def game(self, game_key: str | None) -> dict:
        if not game_key or self.conn is None:
            return {}
        if game_key not in self._games:
            row = self.conn.execute("SELECT game_key, sport, league, title, home_team, away_team, start_time, status "
                                    "FROM games WHERE game_key=?", (game_key,)).fetchone()
            self._games[game_key] = dict(row) if row else {}
        return self._games[game_key]

    def condition(self, condition_id: str | None) -> dict:
        if not condition_id or self.conn is None:
            return {}
        if condition_id not in self._cond:
            row = self.conn.execute("SELECT condition_id, game_key, market_type, question FROM markets "
                                    "WHERE condition_id=?", (condition_id,)).fetchone()
            self._cond[condition_id] = dict(row) if row else {}
        return self._cond[condition_id]

    def outcome(self, token_id: str | None) -> str | None:
        if not token_id or self.conn is None:
            return None
        row = self.conn.execute("SELECT outcome_label FROM tokens WHERE token_id=?", (token_id,)).fetchone()
        return row[0] if row else None

    def marks(self, token_ids: list[str], now: int, max_age: int = 1800) -> dict[str, float]:
        if self.conn is None or not token_ids:
            return {}
        df = C.canonical_prices(self.conn, token_ids, now - max_age, now + 60)
        if df.empty:
            return {}
        last = df.sort_values("ts").groupby("token_id").tail(1)
        return dict(zip(last["token_id"], last["price"].astype(float)))

    def game_minute(self, game_key: str | None) -> float | None:
        if not game_key or self.conn is None:
            return None
        row = self.conn.execute("SELECT game_minute FROM game_states WHERE game_key=? AND game_minute IS NOT NULL "
                                "ORDER BY ts DESC LIMIT 1", (game_key,)).fetchone()
        return float(row[0]) if row else None


def _describe(core: CoreLookup, game_key, condition_id, token_id, outcome_label=None, sport=None, league=None) -> dict:
    cond = core.condition(condition_id)
    g = core.game(game_key or cond.get("game_key"))
    return {"sport": sport or g.get("sport"), "league": league or g.get("league"),
            "game_title": g.get("title") or cond.get("question"),
            "outcome": outcome_label or core.outcome(token_id), "game_key": game_key or cond.get("game_key")}


# ------------------------------------------------------------------ strategy ledgers

def _strategy_rows(paths, vid: str, sql: str, params=()) -> list[dict]:
    conn = C.open_ro(paths.strategy_db(vid))
    if conn is None:
        return []
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def param_history(paths, vid: str) -> list[dict]:
    rows = _strategy_rows(paths, vid, "SELECT version, created_at, params, stake_usdc, mode, author, rationale "
                                      "FROM param_versions ORDER BY version")
    out = []
    for r in rows:
        try:
            params = json.loads(r["params"])
        except (TypeError, json.JSONDecodeError):
            params = None
        out.append({"version": r["version"], "at": C.iso(r["created_at"]), "ts": r["created_at"], "params": params,
                    "stake_usdc": r["stake_usdc"], "mode": r["mode"], "author": r["author"],
                    "rationale": r["rationale"]})
    return out


def stake_events(paths, vid: str) -> list[dict]:
    rows = _strategy_rows(paths, vid, "SELECT ts, from_usdc, to_usdc, from_mode, to_mode, reason, evidence "
                                      "FROM stake_events ORDER BY ts")
    out = []
    for r in rows:
        try:
            ev = json.loads(r["evidence"]) if r["evidence"] else {}
        except json.JSONDecodeError:
            ev = {}
        out.append({"at": C.iso(r["ts"]), "ts": r["ts"], "from_usdc": r["from_usdc"], "to_usdc": r["to_usdc"],
                    "from_mode": r["from_mode"], "to_mode": r["to_mode"], "reason": r["reason"], "evidence": ev})
    return out


def _param_diff(prev: dict | None, cur: dict | None) -> str:
    if not cur:
        return ""
    prev = prev or {}
    parts = [f"{k} {prev.get(k)}→{v}" for k, v in cur.items() if prev.get(k) != v and k in prev]
    parts += [f"+{k}={v}" for k, v in cur.items() if k not in prev and prev]
    return ", ".join(parts)


def changes_in_window(vid: str, history: list[dict], stakes: list[dict], since: int, until: int) -> list[dict]:
    out = []
    for i, h in enumerate(history):
        if since <= (h["ts"] or 0) < until:
            prev = history[i - 1]["params"] if i else None
            diff = _param_diff(prev, h["params"]) if i else "초기 버전"
            out.append({"at": h["at"], "variant_id": vid, "type": "params",
                        "summary": f"{vid} v{h['version']}: {diff or '파라미터 동일'} ({h['author']})",
                        "rationale": h["rationale"]})
    for s in stakes:
        if since <= (s["ts"] or 0) < until:
            mode = f", {s['from_mode']}→{s['to_mode']}" if s["from_mode"] != s["to_mode"] else ""
            out.append({"at": s["at"], "variant_id": vid, "type": "stake",
                        "summary": f"{vid} stake {s['from_usdc']}→{s['to_usdc']} USDC{mode}", "rationale": s["reason"]})
    return out


TX_SQL = """
SELECT o.intent_id, o.position_id, o.created_at, o.mode, o.side, o.token_id, o.condition_id, o.usdc_amount,
       o.shares AS req_shares, o.limit_price, o.status AS order_status,
       f.fill_id, f.ts AS fill_ts, f.price, f.shares, f.fee_usdc, f.status AS fill_status,
       p.status AS position_status, p.exit_reason, p.realized_pnl, p.game_key, p.sport, p.league, p.outcome_label
FROM orders o LEFT JOIN fills f ON f.intent_id = o.intent_id
LEFT JOIN positions p ON p.position_id = o.position_id
WHERE o.created_at >= ? AND o.created_at < ?
ORDER BY o.created_at
"""
RESOLVE_SQL = """
SELECT position_id, closed_at, mode, token_id, condition_id, exit_price, shares, proceeds_usdc, exit_fee_usdc,
       status, exit_reason, realized_pnl, game_key, sport, league, outcome_label, settlement
FROM positions WHERE closed_at >= ? AND closed_at < ? AND exit_reason LIKE 'resolution%'
"""


def _tx_status(r: dict) -> str:
    if r.get("position_status") == "quarantined":
        return "QUARANTINED"
    if r.get("fill_status"):
        return str(r["fill_status"]).upper()
    if r.get("order_status") in ("failed", "cancelled"):
        return "UNFILLED"
    return str(r.get("order_status") or "unknown").upper()


def transactions(paths, variants, core: CoreLookup, since: int, until: int) -> list[dict]:
    out = []
    for v in variants:
        for r in _strategy_rows(paths, v.id, TX_SQL, (since, until)):
            filled = r.get("fill_status") is not None
            price = r["price"] if filled else r.get("limit_price")
            shares = r["shares"] if filled else r.get("req_shares")
            usdc = round(r["price"] * r["shares"], 4) if filled else r.get("usdc_amount")
            desc = _describe(core, r.get("game_key"), r.get("condition_id"), r["token_id"], r.get("outcome_label"),
                             r.get("sport"), r.get("league"))
            out.append({"at": C.iso(r.get("fill_ts") or r["created_at"]), "ts": r.get("fill_ts") or r["created_at"],
                        "variant_id": v.id, "account": v.account, "mode": r["mode"], **desc, "side": r["side"],
                        "price": price, "shares": shares, "usdc": usdc,
                        "fee_usdc": r["fee_usdc"] if filled else None, "status": _tx_status(r),
                        "position_id": r.get("position_id"), "position_status": r.get("position_status"),
                        "exit_reason": r.get("exit_reason"), "realized_pnl": r.get("realized_pnl")})
        for r in _strategy_rows(paths, v.id, RESOLVE_SQL, (since, until)):
            desc = _describe(core, r.get("game_key"), r["condition_id"], r["token_id"], r.get("outcome_label"),
                             r.get("sport"), r.get("league"))
            out.append({"at": C.iso(r["closed_at"]), "ts": r["closed_at"], "variant_id": v.id, "account": v.account,
                        "mode": r["mode"], **desc, "side": "RESOLVE", "price": r["exit_price"], "shares": r["shares"],
                        "usdc": r["proceeds_usdc"], "fee_usdc": r["exit_fee_usdc"],
                        "status": "RESOLVED" if r["realized_pnl"] is not None else "UNSETTLED",
                        "position_id": r["position_id"], "position_status": r["status"],
                        "exit_reason": r["exit_reason"], "realized_pnl": r["realized_pnl"]})
    out.sort(key=lambda x: (x["ts"] or 0, x["variant_id"]))
    return out


def tx_totals(tx: list[dict], settled_by_variant: dict[str, pd.DataFrame], since: int, until: int) -> dict:
    """Per-variant 24h totals. Realised PnL counts positions settled (per performance rules) in the window."""
    out: dict[str, dict] = {}
    for row in tx:
        t = out.setdefault(row["variant_id"], {"tx": 0, "confirmed_buy_usdc": 0.0, "confirmed_sell_usdc": 0.0,
                                               "fees_usdc": 0.0, "fees_unknown": 0, "unfilled": 0,
                                               "quarantined": 0, "settled": 0, "realized_pnl": 0.0})
        t["tx"] += 1
        if row["status"] in ("CONFIRMED", "PAPER"):
            key = "confirmed_buy_usdc" if row["side"] == "BUY" else "confirmed_sell_usdc"
            if row["side"] in ("BUY", "SELL"):
                t[key] = round(t[key] + (row["usdc"] or 0.0), 4)
            if row["fee_usdc"] is None:
                t["fees_unknown"] += 1
            else:
                t["fees_usdc"] = round(t["fees_usdc"] + row["fee_usdc"], 4)
        elif row["status"] == "UNFILLED":
            t["unfilled"] += 1
        elif row["status"] == "QUARANTINED":
            t["quarantined"] += 1
    for vid, s in settled_by_variant.items():
        if s.empty:
            continue
        w = s[(s["closed_at"].astype(int) >= since) & (s["closed_at"].astype(int) < until)]
        if w.empty:
            continue
        t = out.setdefault(vid, {"tx": 0, "confirmed_buy_usdc": 0.0, "confirmed_sell_usdc": 0.0, "fees_usdc": 0.0,
                                 "fees_unknown": 0, "unfilled": 0, "quarantined": 0, "settled": 0,
                                 "realized_pnl": 0.0})
        t["settled"] = int(len(w))
        t["realized_pnl"] = round(float(w["realized_pnl"].sum()), 4)
    return out


def open_position_rows(v, df: pd.DataFrame, core: CoreLookup, now: int) -> list[dict]:
    opens = performance.open_positions(df)
    if opens.empty:
        return []
    marks = core.marks(opens["token_id"].tolist(), now)
    rows = []
    for r in opens.to_dict("records"):
        desc = _describe(core, r.get("game_key"), r["condition_id"], r["token_id"], r.get("outcome_label"),
                         r.get("sport"), r.get("league"))
        mark = marks.get(r["token_id"])
        shares, cost = r.get("shares"), r.get("cost_usdc")
        unreal = round(shares * mark - cost, 4) if mark is not None and shares is not None and cost is not None else None
        rows.append({"variant_id": v.id, "mode": r["mode"], "opened_at": C.iso(r["opened_at"]),
                     "sport": desc["sport"], "league": desc["league"], "title": desc["game_title"],
                     "outcome": desc["outcome"], "entry_price": r.get("entry_price"), "shares": shares,
                     "cost_usdc": cost, "mark_price": mark, "unrealized_pnl": unreal,
                     "game_minute": core.game_minute(desc["game_key"]), "status": r["status"],
                     "position_id": r["position_id"]})
    return rows


def recent_position_rows(s: pd.DataFrame, core: CoreLookup, limit: int = 50) -> list[dict]:
    if s.empty:
        return []
    rows = []
    for r in s.sort_values("closed_at", ascending=False).head(limit).to_dict("records"):
        desc = _describe(core, r.get("game_key"), r["condition_id"], r["token_id"], r.get("outcome_label"),
                         r.get("sport"), r.get("league"))
        rows.append({"opened_at": C.iso(r["opened_at"]), "closed_at": C.iso(r["closed_at"]), "sport": desc["sport"],
                     "title": desc["game_title"], "outcome": desc["outcome"], "entry_price": r.get("entry_price"),
                     "exit_price": r.get("exit_price"), "exit_reason": r.get("exit_reason"),
                     "realized_pnl": r.get("realized_pnl"), "stake_usdc": r.get("stake_usdc"),
                     "game_minute_at_entry": r.get("game_minute_at_entry")})
    return rows


def load_variants() -> tuple[list, str | None]:
    try:
        return registry.load_all(include_off=True), None
    except Exception as exc:  # a broken yaml must not stop reporting
        return [], f"registry: {exc}"


def ladder_view(v, paths, now: int | None = None) -> dict:
    lad = integrations.ladder_status(v, paths, now)
    return {k: lad.get(k) for k in ("status", "action", "trades_at_tier", "needed", "roi_ci_lo", "next_stake_usdc",
                                     "promote_ok", "to_mode", "reason", "available")}


def variant_state(v, paths, core: CoreLookup, now: int, since: int, until: int) -> dict:
    df = performance.load_variant_positions(paths.strategy_db(v.id))
    live, paper = performance.settled(df, "live"), performance.settled(df, "paper")
    primary = live if v.mode == "live" or paper.empty else paper
    history, stakes = param_history(paths, v.id), stake_events(paths, v.id)
    changes = changes_in_window(v.id, history, stakes, since, until)
    last_change = None
    all_changes = changes_in_window(v.id, history, stakes, 0, now + 1)
    if all_changes:
        lc = max(all_changes, key=lambda c: c["at"] or "")
        last_change = {"at": lc["at"], "summary": lc["summary"]}
    return {"id": v.id, "family": v.family, "hypothesis": v.hypothesis, "mode": v.mode, "account": v.account,
            "sports": v.sports, "stake_usdc": v.stake_usdc, "params": v.params, "bounds": v.bounds,
            "limits": v.limits, "yaml": v.to_yaml(), "ladder": ladder_view(v, paths, now),
            "live": performance.summary(live, now), "paper": performance.summary(paper, now),
            "primary_mode": "live" if primary is live else "paper",
            "breakdown": performance.breakdown(primary), "equity_curve": performance.equity_curve(primary),
            "excluded": performance.excluded_counts(df),
            "open": open_position_rows(v, df, core, now), "recent": recent_position_rows(primary, core),
            "param_history": history, "stake_events": stakes, "changes": changes, "last_change": last_change,
            "_settled_live": live, "_settled_paper": paper}


def research_highlights(paths) -> dict:
    from polylab.analysis import calibration  # noqa: PLC0415
    from polylab.analysis.cli import load_cached  # noqa: PLC0415
    cal = load_cached(paths, "calibration") or {}
    ev = load_cached(paths, "events") or {}
    sens = [r for r in ev.get("event_sensitivity", []) if r.get("n", 0) >= 10]
    sens.sort(key=lambda r: -(r.get("mean_abs_jump") or 0))
    return {"calibration_generated_at": cal.get("generated_at"), "events_generated_at": ev.get("generated_at"),
            "calibration_top": calibration.top_gaps(cal.get("calibration", [])),
            "brier": [b for b in cal.get("brier", []) if b.get("phase") != "all"],
            "event_top": sens[:8], "events_measured": ev.get("events_measured"),
            "calibration": cal.get("calibration", []), "event_sensitivity": ev.get("event_sensitivity", []),
            "notes": (cal.get("notes") or []) + (ev.get("notes") or [])}


def build(kind: str, paths, now: int | None = None, slot: str | None = None, use_jenkins: bool = True,
          variants: list | None = None, since: int | None = None) -> dict:
    """`since` extends the window back to the last successful retro of this kind (catch-up after downtime)."""
    now = now or int(time.time())
    if kind == "daily":
        slot = slot or infer_slot(now)
    else:
        slot = None
    default_since, until = window(kind, now)
    since = min(since, default_since) if since is not None and kind != "monthly" else default_since
    stem, date = report_name(kind, now, slot)
    reg_error = None
    if variants is None:
        variants, reg_error = load_variants()
    core = CoreLookup(paths)
    try:
        states = [variant_state(v, paths, core, now, since, until) for v in variants]
        tx_since = since
        tx = transactions(paths, variants, core, tx_since, until)
    finally:
        core.close()
    settled_live = {s["id"]: s["_settled_live"] for s in states}
    settled_paper = {s["id"]: s["_settled_paper"] for s in states}
    settled_any = {vid: (settled_live[vid] if not settled_live[vid].empty else settled_paper[vid])
                   for vid in settled_live}
    by_variant = tx_totals(tx, settled_any, tx_since, until)
    live_rows = performance.all_settled(settled_live, "live")
    h, problems = health_mod.evaluate(paths, now, use_jenkins=use_jenkins)
    totals = {k: round(sum((s["live"]["pnl"][k] or 0.0) for s in states), 4) for k in ("today", "d7", "d30", "all")}
    totals.update({"tx_24h": len(tx) if kind == "daily" else None,
                   "settled_24h": sum(t["settled"] for t in by_variant.values()),
                   "settled_pnl_24h": round(sum(t["realized_pnl"] for t in by_variant.values()), 4),
                   "open_positions": sum(len(s["open"]) for s in states),
                   "unrealized_pnl": round(sum(o["unrealized_pnl"] or 0.0 for s in states for o in s["open"]), 4),
                   "live_variants": sum(1 for s in states if s["mode"] == "live"),
                   "paper_variants": sum(1 for s in states if s["mode"] == "paper")})
    alerts = [{"level": "error" if p["level"] == "critical" else "warn", "at": C.iso(now), "message": p["message"]}
              for p in problems]
    if reg_error:
        alerts.append({"level": "error", "at": C.iso(now), "message": reg_error[:300]})
    for s in states:
        s.pop("_settled_live")
        s.pop("_settled_paper")
    return {"kind": kind, "slot": slot, "name": stem, "date": date, "title": title_for(kind, now, slot),
            "generated_at": C.iso(now), "now": now, "window": {"since": C.iso(since), "until": C.iso(until),
                                                               "tx_since": C.iso(tx_since)},
            "git_commit": git_commit(), "totals": totals, "variants": states, "transactions": tx,
            "tx_by_variant": by_variant, "open_positions": [o for s in states for o in s["open"]],
            "health": h, "alerts": alerts, "research": research_highlights(paths),
            "stake_tiers": performance.tier_stats(live_rows),
            "changes": sorted([c for s in states for c in s["changes"]], key=lambda c: c["at"] or ""),
            "ai": {"ran": False, "reason": None}}


def to_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=1, default=str)


def write(report: dict, out_dir: Path, markdown: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"{report['name']}.md"
    js = out_dir / f"{report['name']}.json"
    md.write_text(markdown)
    js.write_text(to_json(report))
    return md, js
