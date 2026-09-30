"""Deterministic "games of the window" view: which games were played, how their win probabilities
moved, and the upsets. Feeds the daily report section, the Slack summary and latest/games_24h.json.

Definitions (1-minute canonical bars, poll_mid > ws_last > history; bar ts = minute start):
  play window   = [start_time, end_of_play). end_of_play = games.ended_at (if after start), else the
                  first ended game_states row, else start + nominal duration for finished games, else
                  min(until, start + max duration) for a game still running. Settlement drift after the
                  whistle is outside the play window, so it never counts as an in-play swing.
  pre_price     = last bar that closed before kickoff (ts + 60 <= start_time, within PRE_LOOKBACK_S).
  min/max       = over bars inside the play window.
  final_price   = last bar before market resolution (before `until` if unresolved).
  swing_1m      = largest |p(b) - p(a)| between consecutive in-play bars at most 2 minutes apart.
  swing_10m     = largest |p(b) - p(a)| between in-play bars with 0 < ts(b) - ts(a) <= 10 minutes.
                  Both swings skip moves that end converged (p(b) <= 0.03 or >= 0.97) in the last 10 minutes
                  of play: that is the result settling (and ended_at can lag the whistle by a discover cycle),
                  not an in-play re-pricing.
  favourite     = outcome with the highest pre_price; upset = resolved game the favourite did not win
                  (a soccer draw counts as a favourite loss).
Missing data is null, never 0.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from polylab.analysis import _common as C
from polylab.analysis import events as events_mod
from polylab.collector import common as collector_common

OUTCOME_SIDES = ("home", "draw", "away")
NOTABLE_SWING = 0.20
PRE_LOOKBACK_S = 12 * 3600
PATH_POINTS = 120
PATH_PRE_S = 3600                      # the chart path starts 1h before kickoff
MAX_MD_GAMES = 30
CONVERGED = 0.03                       # price within this of 0/1 = result decided
SETTLE_TAIL_S = 600
ENDED_STATUSES = ("ended", "final", "closed", "ft", "vft", "aet", "pen")


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f


def _r(v, nd: int = 4) -> float | None:
    v = _num(v)
    return None if v is None else round(v, nd)


# ------------------------------------------------------------------ selection

def _candidate_games(conn: sqlite3.Connection, since: int, until: int) -> list[dict]:
    widest = max(collector_common.MAX_GAME_SECONDS.values())
    sports = list(C.SPORTS)
    rows = conn.execute(
        f"SELECT game_key, sport, league, title, home_team, away_team, start_time, status, home_score, away_score, "
        f"ended_at FROM games WHERE sport IN ({','.join('?' * len(sports))}) AND start_time IS NOT NULL "
        "AND start_time < ? AND start_time >= ? ORDER BY start_time, game_key",
        (*sports, until, since - widest)).fetchall()
    return [dict(r) for r in rows]


def _state_end(conn: sqlite3.Connection, game_key: str, start: int, until: int) -> int | None:
    rows = conn.execute("SELECT ts, status, ended FROM game_states WHERE game_key=? AND ts>? AND ts<? ORDER BY ts",
                        (game_key, start, until)).fetchall()
    for r in rows:
        if r["ended"] == 1 or str(r["status"] or "").lower() in ENDED_STATUSES:
            return int(r["ts"])
    return None


def _end_of_play(conn, g: dict, until: int) -> tuple[int, str]:
    start = int(g["start_time"])
    ended_at = g.get("ended_at")
    if ended_at is not None and int(ended_at) > start:
        return min(int(ended_at), until), "ended_at"
    st = _state_end(conn, g["game_key"], start, until)
    if st is not None:
        return st, "game_state"
    if str(g.get("status") or "").lower() in ENDED_STATUSES:
        return min(start + collector_common.NOMINAL_GAME_SECONDS.get(g["sport"], 3 * 3600), until), "nominal"
    return min(start + collector_common.MAX_GAME_SECONDS.get(g["sport"], 5 * 3600), until), "running"


def select_games(conn: sqlite3.Connection, since: int, until: int, soccer_leagues: frozenset[str] | set[str],
                 traded_keys: set[str] | frozenset[str] = frozenset()) -> tuple[list[dict], dict]:
    """Games live or finished inside [since, until), and a count of out-of-scope soccer games."""
    out, excluded = [], {}
    for g in _candidate_games(conn, since, until):
        if str(g.get("status") or "").lower() == "cancelled":
            continue
        end, end_source = _end_of_play(conn, g, until)
        if end < since:
            continue
        league = (g.get("league") or "").lower()
        if g["sport"] == "soccer" and league not in soccer_leagues and g["game_key"] not in traded_keys:
            excluded[league or "unknown"] = excluded.get(league or "unknown", 0) + 1
            continue
        g["end_of_play"], g["end_source"] = end, end_source
        g["traded"] = g["game_key"] in traded_keys
        out.append(g)
    return out, dict(sorted(excluded.items()))


def outcome_tokens(conn: sqlite3.Connection, game_keys: list[str]) -> dict[str, list[dict]]:
    """game_key -> [{side, label, token_id, condition_id, outcome_index, volume, resolved_*}] (home, draw, away)."""
    out: dict[str, dict[str, dict]] = {}
    for group in C._chunks(sorted(set(game_keys))):
        rows = conn.execute(
            f"""SELECT m.game_key, m.condition_id, m.market_type, m.group_item_title, m.volume,
                       m.resolved_outcome_index, m.resolved_at, t.token_id, t.outcome_index, t.outcome_label, t.side
                FROM markets m JOIN tokens t ON t.condition_id = m.condition_id
                WHERE m.game_key IN ({','.join('?' * len(group))}) AND m.market_type IN ('moneyline', 'draw')
                  AND t.side IN ('home', 'draw', 'away')
                ORDER BY m.game_key, t.side, COALESCE(m.volume, 0) DESC, t.token_id""", group).fetchall()
        for r in rows:
            sides = out.setdefault(r["game_key"], {})
            if r["side"] in sides:  # keep the highest-volume market per side
                continue
            label = r["outcome_label"]
            if label in (None, "", "Yes") and r["group_item_title"]:
                label = r["group_item_title"]
            sides[r["side"]] = {"side": r["side"], "label": label, "token_id": r["token_id"],
                                "condition_id": r["condition_id"], "outcome_index": r["outcome_index"],
                                "volume": _num(r["volume"]), "resolved_outcome_index": r["resolved_outcome_index"],
                                "resolved_at": r["resolved_at"]}
    return {k: [v[s] for s in OUTCOME_SIDES if s in v] for k, v in out.items()}


# ------------------------------------------------------------------ price metrics

def _won(o: dict, until: int) -> bool | None:
    idx, at = o.get("resolved_outcome_index"), o.get("resolved_at")
    if idx is None or (at is not None and int(at) > until):
        return None
    return int(o["outcome_index"]) == int(idx)


def max_swing(bars: list[tuple[int, float, str]], window_s: int, max_gap_s: int | None = None,
              settle_from: int | None = None) -> dict | None:
    """Largest |Δ| between bars within `window_s` (consecutive bars only when max_gap_s is set).
    Moves ending converged at or after `settle_from` are skipped. Ties keep the earliest end bar."""
    best = None
    for i in range(1, len(bars)):
        ts_b, p_b, src_b = bars[i]
        if settle_from is not None and ts_b >= settle_from and (p_b <= CONVERGED or p_b >= 1 - CONVERGED):
            continue
        lo = i - 1 if max_gap_s is not None else 0
        for j in range(i - 1, lo - 1, -1):
            ts_a, p_a, src_a = bars[j]
            gap = ts_b - ts_a
            if gap > (max_gap_s if max_gap_s is not None else window_s):
                break
            d = p_b - p_a
            if best is None or abs(d) > abs(best["delta"]) + 1e-12:
                best = {"delta": round(d, 4), "from_price": round(p_a, 4), "to_price": round(p_b, 4),
                        "from_ts": ts_a, "ts": ts_b, "sources": f"{src_a}>{src_b}"}
    return best


def downsample(bars: list[tuple[int, float, str]], lo: int, hi: int, points: int = PATH_POINTS) -> list[list]:
    """≤ `points` [iso, price] pairs: the last bar of each equal-width time bin in [lo, hi)."""
    sel = [(ts, p) for ts, p, _ in bars if lo <= ts < hi]
    if len(sel) <= points:
        return [[C.iso(ts), round(p, 4)] for ts, p in sel]
    width = (hi - lo) / points
    last: dict[int, tuple[int, float]] = {}
    for ts, p in sel:
        last[min(int((ts - lo) // width), points - 1)] = (ts, p)
    return [[C.iso(ts), round(p, 4)] for _, (ts, p) in sorted(last.items())]


def _outcome_metrics(o: dict, bars: list[tuple[int, float, str]], start: int, end: int, until: int) -> dict:
    pre = [b for b in bars if start - PRE_LOOKBACK_S <= b[0] and b[0] + 60 <= start]
    play = [b for b in bars if start <= b[0] and b[0] + 60 <= end]
    won = _won(o, until)
    res_at = o.get("resolved_at")
    final_bound = int(res_at) if won is not None and res_at is not None else until
    fin = [b for b in bars if b[0] < final_bound]
    prices = [p for _, p, _ in play]
    return {"side": o["side"], "label": o["label"], "token_id": o["token_id"], "condition_id": o["condition_id"],
            "volume_usd": o["volume"], "pre_price": _r(pre[-1][1]) if pre else None,
            "min_price": _r(min(prices)) if prices else None, "max_price": _r(max(prices)) if prices else None,
            "final_price": _r(fin[-1][1]) if fin else None, "won": won, "in_play_bars": len(play),
            "swing_1m": max_swing(play, 60, max_gap_s=120, settle_from=end - SETTLE_TAIL_S),
            "swing_10m": max_swing(play, 600, settle_from=end - SETTLE_TAIL_S),
            "path": downsample(bars, start - PATH_PRE_S, min(final_bound, until) + 60)}


def _minutes_at(conn, games: list[dict], points: list[tuple[str, int]]) -> dict[tuple[str, int], tuple]:
    """(feed game minute, period) at (game_key, ts) via the shared as-of join (soccer clock extrapolated,
    stale clocks and periods dropped)."""
    if not points:
        return {}
    sport = {g["game_key"]: g["sport"] for g in games}
    obs = pd.DataFrame(sorted(set(points)), columns=["game_key", "ts"])
    obs["sport"] = obs["game_key"].map(sport)
    states = C.game_states(conn, sorted({k for k, _ in points}))
    merged = C.attach_state(obs, states)
    out = {}
    for r in merged.itertuples(index=False):
        fresh = isinstance(r.period, str) and _num(r.state_ts) is not None \
            and r.ts - r.state_ts <= C.STALE_CLOCK_MIN["other"] * 60
        out[(r.game_key, int(r.ts))] = (_r(r.game_minute, 1), r.period if fresh else None)
    return out


def _score(conn, g: dict, until: int) -> tuple[int | None, int | None]:
    if g.get("home_score") is not None and g.get("away_score") is not None \
            and str(g.get("status") or "").lower() in ENDED_STATUSES:
        return int(g["home_score"]), int(g["away_score"])
    r = conn.execute("SELECT home_score, away_score FROM game_states WHERE game_key=? AND ts<? "
                     "AND home_score IS NOT NULL ORDER BY ts DESC LIMIT 1", (g["game_key"], until)).fetchone()
    return (int(r[0]), int(r[1])) if r else (g.get("home_score"), g.get("away_score"))


def _events(conn, games: list[dict], until: int) -> dict[str, list[dict]]:
    keys = [g["game_key"] for g in games]
    if not keys:
        return {}
    states = C.game_states(conn, keys)
    states = states[states["ts"].astype(int) < until] if not states.empty else states
    gdf = pd.DataFrame([{"game_key": g["game_key"], "sport": g["sport"], "start_time": g["start_time"]} for g in games])
    ev = events_mod.detect_score_changes(states, gdf)
    out: dict[str, list[dict]] = {}
    for r in ev.itertuples(index=False):
        gm = _r(r.game_minute, 1)
        out.setdefault(r.game_key, []).append({
            "at": C.iso(int(r.ts)), "game_minute": gm, "minute_source": getattr(r, "minute_source", "feed"),
            "scorer": r.scorer, "home_score": int(r.home_score), "away_score": int(r.away_score)})
    return out


# ------------------------------------------------------------------ assembly

def _game_row(g: dict, outs: list[dict], score, events: list[dict]) -> dict:
    by_side = {o["side"]: o for o in outs}
    priced = [o for o in outs if o["pre_price"] is not None]
    fav = max(priced, key=lambda o: (o["pre_price"], -OUTCOME_SIDES.index(o["side"]))) if priced else None
    winners = [o["side"] for o in outs if o["won"] is True]
    resolved = bool(outs) and all(o["won"] is not None for o in outs) and len(winners) == 1
    result = winners[0] if resolved else None
    upset = bool(fav and resolved and fav["won"] is False)
    swings = [o["swing_10m"] for o in outs if o["swing_10m"]]
    top = min(swings, key=lambda s: (-abs(s["delta"]), s["at"])) if swings else None
    notable = bool(top and abs(top["delta"]) >= NOTABLE_SWING - 1e-9)
    vol = sum(v for v in {o["condition_id"]: o["volume_usd"] for o in outs}.values() if v is not None) if outs else None
    return {"game_key": g["game_key"], "sport": g["sport"], "league": g.get("league"), "title": g.get("title"),
            "home_team": g.get("home_team"), "away_team": g.get("away_team"),
            "start_time": C.iso(g["start_time"]), "end_of_play": C.iso(g["end_of_play"]),
            "end_source": g["end_source"], "status": g.get("status"),
            "home_score": score[0], "away_score": score[1], "resolved": resolved, "result": result,
            "volume_usd": _r(vol, 2), "traded": g["traded"],
            "favourite": fav["side"] if fav else None, "favourite_pre_price": fav["pre_price"] if fav else None,
            "upset": upset, "notable_swing": notable,
            "max_swing_10m": top["delta"] if top else None,
            "outcomes": [by_side[s] for s in OUTCOME_SIDES if s in by_side],
            "events": events}


def _swing_view(s: dict | None, g: dict, minutes: dict) -> dict | None:
    if not s:
        return None
    minute, period = minutes.get((g["game_key"], s["ts"]), (None, None))
    return {"delta": s["delta"], "from_price": s["from_price"], "to_price": s["to_price"], "at": C.iso(s["ts"]),
            "from_at": C.iso(s["from_ts"]), "game_minute": minute, "period": period,
            "elapsed_min": round((s["ts"] - int(g["start_time"])) / 60.0, 1), "sources": s["sources"]}


def sport_summary(rows: list[dict]) -> list[dict]:
    out = []
    for sport in C.SPORTS:
        g = [r for r in rows if r["sport"] == sport]
        if not g:
            continue
        fav = [r for r in g if r["resolved"] and r["favourite"] is not None]
        fav_wins = sum(1 for r in fav if not r["upset"])
        swings = [abs(r["max_swing_10m"]) for r in g if r["max_swing_10m"] is not None]
        out.append({"sport": sport, "games": len(g), "resolved": sum(1 for r in g if r["resolved"]),
                    "favourites_resolved": len(fav),
                    "favourite_win_rate": round(fav_wins / len(fav), 4) if fav else None,
                    "favourite_avg_pre_price": round(sum(r["favourite_pre_price"] for r in fav) / len(fav), 4)
                    if fav else None,
                    "avg_max_swing_10m": round(sum(swings) / len(swings), 4) if swings else None,
                    "upsets": sum(1 for r in g if r["upset"]), "notable_swings": sum(1 for r in g if r["notable_swing"])})
    return out


def build(paths, since: int, until: int, traded_keys: set[str] | frozenset[str] = frozenset(),
          soccer_leagues: frozenset[str] | set[str] | None = None) -> dict:
    """JSON-ready dict (ISO times) for the report section and latest/games_24h.json."""
    leagues = frozenset(soccer_leagues if soccer_leagues is not None else collector_common.load_config().soccer_leagues)
    base = {"generated_at": C.iso(until), "window": {"since": C.iso(since), "until": C.iso(until)},
            "scope": {"sports": list(C.SPORTS), "soccer_leagues": sorted(leagues),
                      "note": "soccer outside the league scope is included only when a strategy traded it"},
            "notable_swing": NOTABLE_SWING, "excluded_out_of_scope": {}, "summary_by_sport": [], "games": []}
    conn = C.open_ro(paths.core_db)
    if conn is None:
        return {**base, "error": "core.db missing"}
    try:
        games, excluded = select_games(conn, since, until, leagues, traded_keys)
        toks = outcome_tokens(conn, [g["game_key"] for g in games])
        token_ids = [o["token_id"] for outs in toks.values() for o in outs]
        lo = min((int(g["start_time"]) for g in games), default=until) - PRE_LOOKBACK_S
        prices = C.canonical_prices(conn, token_ids, lo, until) if token_ids else pd.DataFrame()
        bars: dict[str, list[tuple[int, float, str]]] = {}
        if not prices.empty:
            prices = prices.sort_values(["token_id", "ts"])
            for tok, grp in prices.groupby("token_id", sort=True):
                bars[tok] = list(zip(grp["ts"].astype(int), grp["price"].astype(float), grp["source"]))
        metrics = {}
        points = []
        for g in games:
            start, end = int(g["start_time"]), int(g["end_of_play"])
            metrics[g["game_key"]] = [_outcome_metrics(o, bars.get(o["token_id"], []), start, end, until)
                                      for o in toks.get(g["game_key"], [])]
            points += [(g["game_key"], m[k]["ts"]) for m in metrics[g["game_key"]] for k in ("swing_1m", "swing_10m")
                       if m[k]]
        minutes = _minutes_at(conn, games, points)
        evs = _events(conn, games, until)
        rows = []
        for g in games:
            outs = metrics[g["game_key"]]
            for o in outs:
                o["swing_1m"] = _swing_view(o["swing_1m"], g, minutes)
                o["swing_10m"] = _swing_view(o["swing_10m"], g, minutes)
            rows.append(_game_row(g, outs, _score(conn, g, until), evs.get(g["game_key"], [])))
    finally:
        conn.close()
    rows.sort(key=lambda r: (C.SPORTS.index(r["sport"]), r["league"] or "", r["start_time"] or "", r["game_key"]))
    return {**base, "excluded_out_of_scope": excluded, "summary_by_sport": sport_summary(rows), "games": rows}


def top_games(rows: list[dict], limit: int = MAX_MD_GAMES) -> list[dict]:
    """Flagged games first, then by volume; returned in sport → league → kickoff order."""
    ranked = sorted(rows, key=lambda r: (not (r["upset"] or r["notable_swing"] or r["traded"]),
                                         -(r["volume_usd"] or 0.0), r["game_key"]))[:limit]
    keep = {r["game_key"] for r in ranked}
    return [r for r in rows if r["game_key"] in keep]


def biggest_swing(rows: list[dict]) -> tuple[dict, dict] | None:
    best = None
    for r in rows:
        for o in r["outcomes"]:
            s = o["swing_10m"]
            if s and (best is None or abs(s["delta"]) > abs(best[1]["swing_10m"]["delta"]) + 1e-12):
                best = (r, o)
    return best


def when(s: dict, sport: str) -> str:
    """Short game-time label for a swing: soccer minute, else feed period (+ clock), else wall-clock."""
    if sport == "soccer" and s.get("game_minute") is not None:
        return f"{s['game_minute']:.0f}'"
    if s.get("period"):
        clock = f" {s['game_minute']:.0f}'" if sport != "mlb" and s.get("game_minute") is not None else ""
        return f"{s['period']}{clock}"
    return f"+{s['elapsed_min']:.0f}m"
