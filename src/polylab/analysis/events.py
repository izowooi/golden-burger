"""Event study: price reaction of the scoring team's whole-game token to a score change.

This is the core "sensitivity by game time" result: the same event (a goal / score) is
bucketed by game minute and score state, and we measure how far the price jumps and
whether it reverts afterwards.

Definitions (1-minute bars, bar ts = minute start, m = minute containing the event):
  pre     = canonical price at m-1 (last bar within 10 min before m if missing)
  post    = price at m+1 (first fully post-event bar, within 2 min)
  jump    = post - pre, from the scorer's perspective (expected > 0)
  delta_k = price(m+k) - pre            for k in 1, 5, 10 minutes
  reversion_k = price(m+1+k) - post     for k in 1, 5, 10 (negative = gives back the jump)
  peak    = max price in [m, m+10], peak_minute = minutes after m
10s/30s deltas are not derivable from 1-minute bars and are reported as null.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from polylab.analysis import _common as C

EVENT_NAME = {"soccer": "goal", "nhl": "goal", "mlb": "run", "nba": "score", "nfl": "score"}
NOTE = ("event = single-side score increase in game_states; token = scoring team's whole-game moneyline token "
        "(home/away side, or the 'Yes' token of the team's binary market); jump = p(m+1)-p(m-1); "
        "reversion_k = p(m+1+k)-p(m+1) (negative = reverts); 10s/30s deltas null at 1-minute resolution; "
        "NBA/NFL events often overlap within a minute, see n_isolated")


def detect_score_changes(states: pd.DataFrame, games: pd.DataFrame | None = None) -> pd.DataFrame:
    """Single-side score increases. `games` (game_key, sport, start_time) supplies sport/timing."""
    cols = ["game_key", "ts", "scorer", "points", "game_minute", "home_score", "away_score",
            "diff_before", "isolated"]
    if states.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for game_key, g in states.sort_values("ts").groupby("game_key"):
        g = g.dropna(subset=["home_score", "away_score"])
        # Running maxima: interleaved sources (ws_sports vs a lagging gamma poll) can briefly show an
        # older, lower score; only a new maximum on one side is a scoring event.
        best = None
        for r in g.itertuples(index=False):
            h, a = int(r.home_score), int(r.away_score)
            if best is None:
                best = (h, a)
                continue
            dh, da = h - best[0], a - best[1]
            if dh > 0 and da > 0:  # both sides moved in one update: ambiguous, skip but advance
                best = (max(h, best[0]), max(a, best[1]))
                continue
            if dh > 0 or da > 0:
                scorer = "home" if dh > 0 else "away"
                before = best[0] - best[1] if scorer == "home" else best[1] - best[0]
                gm = r.game_minute
                rows.append({"game_key": game_key, "ts": int(r.ts), "scorer": scorer, "points": max(dh, da),
                             "game_minute": float(gm) if gm is not None and not pd.isna(gm) else np.nan,
                             "home_score": max(h, best[0]), "away_score": max(a, best[1]),
                             "diff_before": int(before)})
                best = (max(h, best[0]), max(a, best[1]))
    ev = pd.DataFrame(rows, columns=cols[:-1])
    if ev.empty:
        return pd.DataFrame(columns=cols)
    ev = ev.sort_values(["game_key", "ts"]).reset_index(drop=True)
    gap_prev = ev.groupby("game_key")["ts"].diff()
    gap_next = -ev.groupby("game_key")["ts"].diff(-1)
    ev["isolated"] = (gap_prev.isna() | (gap_prev > 180)) & (gap_next.isna() | (gap_next > 180))
    if games is not None and not games.empty:
        ev = ev.merge(games[["game_key", "sport", "start_time"]], on="game_key", how="left")
        missing = ev["game_minute"].isna()
        if missing.any():
            frac = C.fraction_played(ev.loc[missing, "sport"], ev.loc[missing, "ts"],
                                     ev.loc[missing, "start_time"], ev.loc[missing, "game_minute"])
            ev.loc[missing, "game_minute"] = frac * ev.loc[missing, "sport"].map(C.REGULATION)
            ev["minute_source"] = np.where(missing, "wall_clock", "feed")
        else:
            ev["minute_source"] = "feed"
    return ev


def scorer_tokens(conn: sqlite3.Connection, game_keys: list[str]) -> dict[tuple[str, str], str]:
    """(game_key, home|away) -> token_id of that team's whole-game win token."""
    out: dict[tuple[str, str], str] = {}
    if not game_keys:
        return out
    for group in C._chunks(sorted(set(game_keys))):
        df = C.read_sql(conn, f"""
            SELECT m.game_key, m.condition_id, m.group_item_title, m.question, m.volume,
                   t.token_id, t.side, t.outcome_label, g.home_team, g.away_team
            FROM markets m JOIN tokens t ON t.condition_id = m.condition_id
            JOIN games g ON g.game_key = m.game_key
            WHERE m.market_type = 'moneyline' AND m.game_key IN ({','.join('?' * len(group))})
            ORDER BY m.volume DESC
        """, group)
        for r in df.itertuples(index=False):
            side = (r.side or "").lower()
            if side in ("home", "away"):  # collector maps team tokens (and soccer "Yes" tokens) to a side
                out.setdefault((r.game_key, side), r.token_id)
                continue
            if side in ("no", "draw", "over", "under"):
                continue
            label = (r.group_item_title or r.question or "") if side == "yes" else (r.outcome_label or "")
            label = label.lower()
            hits = [ts for ts, team in (("home", r.home_team), ("away", r.away_team)) if team and team.lower() in label]
            if len(hits) == 1:  # a label naming both teams is ambiguous
                out.setdefault((r.game_key, hits[0]), r.token_id)
    return out


def _price_at(series: pd.Series, ts: int, lookback: int = 0, lookahead: int = 0) -> float | None:
    """Exact minute, else nearest within [ts-lookback, ts+lookahead] (earlier first when looking back)."""
    if ts in series.index:
        return float(series.loc[ts])
    if lookahead:
        window = series.loc[ts:ts + lookahead]
        if len(window):
            return float(window.iloc[0])
    if lookback:
        window = series.loc[ts - lookback:ts]
        if len(window):
            return float(window.iloc[-1])
    return None


def measure_reactions(events: pd.DataFrame, prices: pd.DataFrame,
                      tokens: dict[tuple[str, str], str]) -> pd.DataFrame:
    rows = []
    by_token = {t: g.set_index("ts")["price"].sort_index() for t, g in prices.groupby("token_id")}
    for ev in events.itertuples(index=False):
        token = tokens.get((ev.game_key, ev.scorer))
        s = by_token.get(token) if token else None
        if s is None or s.empty:
            continue
        m = int(ev.ts) // 60 * 60
        pre = _price_at(s, m - 60, lookback=540)
        post = _price_at(s, m + 60, lookahead=60)
        if pre is None or post is None:
            continue
        rec = {"game_key": ev.game_key, "ts": int(ev.ts), "token_id": token, "sport": getattr(ev, "sport", None),
               "scorer": ev.scorer, "points": ev.points, "game_minute": ev.game_minute,
               "diff_before": ev.diff_before, "isolated": bool(ev.isolated),
               "pre_price": pre, "post_price": post, "jump": post - pre,
               "delta_10s": None, "delta_30s": None}
        for k in (1, 5, 10):
            pk = _price_at(s, m + k * 60, lookback=60)
            rec[f"delta_{k}m"] = None if pk is None else pk - pre
            pr = _price_at(s, m + 60 + k * 60, lookback=60)
            rec[f"reversion_{k}m"] = None if pr is None else pr - post
        window = s.loc[m:m + 600]
        rec["peak_price"] = float(window.max()) if len(window) else None
        rec["peak_minute"] = float((window.idxmax() - m) / 60) if len(window) else None
        rows.append(rec)
    return pd.DataFrame(rows)


def _score_state(diff_before: int) -> str:
    return "trailing" if diff_before < 0 else ("level" if diff_before == 0 else "leading")


def _agg(g: pd.DataFrame) -> dict:
    def mean(col):
        v = pd.to_numeric(g[col], errors="coerce").dropna()
        return round(float(v.mean()), 4) if len(v) else None
    jumps = pd.to_numeric(g["jump"], errors="coerce")
    return {"n": int(len(g)), "n_isolated": int(g["isolated"].sum()),
            "mean_abs_jump": round(float(jumps.abs().mean()), 4),
            "median_jump": round(float(jumps.median()), 4),
            "mean_jump": round(float(jumps.mean()), 4),
            "mean_pre_price": mean("pre_price"),
            "reversion_1m": mean("reversion_1m"), "reversion_5m": mean("reversion_5m"),
            "reversion_10m": mean("reversion_10m"), "mean_peak_minute": mean("peak_minute")}


def aggregate(reactions: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """(event_sensitivity by sport x minute bucket, by sport x minute bucket x score state)."""
    if reactions.empty:
        return [], []
    r = reactions.copy()
    r["event"] = r["sport"].map(EVENT_NAME).fillna("score")
    r["minute_bucket"] = [C.minute_bucket(s, m) for s, m in zip(r["sport"], r["game_minute"])]
    r = r[r["minute_bucket"].notna()]
    r["score_state"] = r["diff_before"].map(_score_state)

    def bucket_key(b: str) -> float:
        return float(b.rstrip("+").split("-")[0])

    by_minute, by_state = [], []
    for (sport, event, mb), g in sorted(r.groupby(["sport", "event", "minute_bucket"]),
                                        key=lambda kv: (kv[0][0], bucket_key(kv[0][2]))):
        by_minute.append({"sport": sport, "event": event, "minute_bucket": mb, **_agg(g)})
        for state, gs in g.groupby("score_state"):
            by_state.append({"sport": sport, "event": event, "minute_bucket": mb,
                             "score_state": state, **_agg(gs)})
    return by_minute, by_state


def run(conn: sqlite3.Connection, since: int | None = None, until: int | None = None,
        all_leagues: bool = False) -> dict:
    lg_sql, lg_params = C.league_filter_sql("g", all_leagues)
    games = C.read_sql(conn, "SELECT g.game_key, g.sport, g.start_time FROM games g WHERE 1=1"
                       + (" AND g.start_time >= ?" if since is not None else "")
                       + (" AND g.start_time < ?" if until is not None else "") + lg_sql,
                       [x for x in (since, until) if x is not None] + lg_params)
    result = {"event_sensitivity": [], "by_score_state": [], "events_detected": 0,
              "events_measured": 0, "notes": [NOTE] + ([] if all_leagues else [C.LEAGUE_NOTE])}
    if games.empty:
        return result
    states = C.game_states(conn, games["game_key"].tolist())
    events = detect_score_changes(states, games)
    result["events_detected"] = int(len(events))
    if events.empty:
        return result
    tokens = scorer_tokens(conn, events["game_key"].unique().tolist())
    lo, hi = int(events["ts"].min()) - 900, int(events["ts"].max()) + 1500
    prices = C.canonical_prices(conn, list(set(tokens.values())), lo, hi)
    reactions = measure_reactions(events, prices, tokens)
    result["events_measured"] = int(len(reactions))
    result["event_sensitivity"], result["by_score_state"] = aggregate(reactions)
    return result
