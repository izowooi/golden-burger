"""`polylab discover` — find upcoming (-6h..+120h, CollectorConfig.lookahead_s) and live games per sport and upsert games/markets/tokens.

Sources (probed 2026-09-30, see docs/research/api-sources.md):
1. Gamma `GET /markets/keyset?tag_id&closed=false&sports_market_types=moneyline&end_date_min/max`
   — light payload. For soccer `endDate == gameStartTime`, so the end-date window stays small (+50h: 3 pages, ~2 s);
   US sports use a +10 day window (MLB playoff moneylines carry endDate = start + 7 days).
   We then filter client-side on `gameStartTime`.
2. Gamma `GET /markets/keyset?...&sports_market_types=totals&sports_market_types=spreads&volume_num_min=<floor>`
   — only game-level lines that already reached the volume floor (soccer lines live in the child
   "<game> - More Markets" event; they are joined to the game through `events[0].gameId`).
3. Gamma `GET /events/keyset?id=..` for the main events — teams[].ordering (home/away), gameId, live/ended,
   score (title order), period, elapsed, finishedTimestamp. (`/events/keyset` by start_time is ~32 MB
   per soccer sweep because child events embed every prop; avoided.)
4. Closing/resolution: Gamma `GET /markets/keyset?condition_ids=..&closed=true` for tracked open markets of
   started games (closed rows are invisible without closed=true); winner = outcomePrices "1" with
   umaResolutionStatus resolved, else Data API `/v2/resolutions` payouts.
Idempotent: every write is an upsert; running it twice changes only updated_at.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict

from polylab import db as dbmod
from polylab.api import data_api, gamma
from polylab.api.http import Client, default_client
from polylab.collector import common as C

log = logging.getLogger("polylab.discover")

US_END_DATE_SLACK_S = 10 * 86400
SOCCER_END_DATE_SLACK_S = 2 * 3600


def discover(conn, cfg: C.CollectorConfig | None = None, client: Client | None = None, ts: int | None = None) -> dict:
    cfg = cfg or C.load_config()
    client = client or default_client()
    ts = ts or C.now()
    summary: dict = {"sports": {}}
    for sport in cfg.sports:
        t0 = time.time()
        try:
            summary["sports"][sport] = discover_sport(conn, sport, cfg, client, ts)
        except Exception as exc:  # one sport failing must not block the others
            log.exception("discover %s failed", sport)
            conn.rollback()
            dbmod.quality(conn, "discover_failed", sport, repr(exc)[:500])
            conn.commit()
            summary["sports"][sport] = {"error": repr(exc)[:300]}
        summary["sports"][sport]["secs"] = round(time.time() - t0, 2)
    t0 = time.time()
    summary["closing"] = refresh_closed(conn, client, ts)
    summary["closing"]["secs"] = round(time.time() - t0, 2)
    return summary


def discover_sport(conn, sport: str, cfg: C.CollectorConfig, client: Client, ts: int) -> dict:
    tag = C.SPORT_TAGS[sport]
    slack = SOCCER_END_DATE_SLACK_S if sport == "soccer" else US_END_DATE_SLACK_S
    lo, hi = ts - cfg.lookback_s, ts + cfg.lookahead_s
    ml = gamma.markets_keyset({
        "tag_id": tag, "closed": "false", "sports_market_types": "moneyline",
        "end_date_min": gamma.iso(lo - 6 * 3600), "end_date_max": gamma.iso(hi + slack),
    }, client)
    tracked = {r[0] for r in conn.execute(
        "SELECT game_key FROM games WHERE sport=? AND COALESCE(status,'') NOT IN ('ended','cancelled') "
        "AND start_time >= ?", (sport, ts - 2 * 86400))}

    by_event: dict[str, list[dict]] = defaultdict(list)
    for m in ml:
        ev = (m.get("events") or [{}])[0]
        eid = str(ev.get("id")) if ev.get("id") is not None else None
        if not eid or not m.get("conditionId"):
            continue
        start = gamma.parse_time(m.get("gameStartTime")) or gamma.parse_time(ev.get("startTime"))
        if eid in tracked or (start is not None and lo <= start <= hi):
            by_event[eid].append(m)

    events = {str(e["id"]): e for e in gamma.events_by_ids(sorted(set(by_event) | tracked), client)}
    games_upserted = markets_upserted = skipped_filter = 0
    game_by_gid: dict[str, tuple[str, C.Teams]] = {}
    with dbmod.tx(conn):
        for eid, markets in by_event.items():
            ev = events.get(eid)
            if ev is None:
                continue
            league = C.league_code(ev, markets[0])
            ml_volume = sum(C.market_volume(m) for m in markets)
            if sport == "soccer" and eid not in tracked and not C.soccer_game_included(league, ml_volume, cfg):
                skipped_filter += 1
                continue
            row = C.game_row(ev, sport, source="discover",
                             fallback_start=gamma.parse_time(markets[0].get("gameStartTime")))
            C.upsert_game(conn, row, ts)
            games_upserted += 1
            teams = C.teams_of(ev, sport)
            if row["polymarket_game_id"]:
                game_by_gid[row["polymarket_game_id"]] = (eid, teams)
            for m in markets:
                mt = C.market_type_of(m)
                if mt is None:
                    continue
                C.upsert_market(conn, C.market_row(m, game_key=eid, market_type=mt), ts)
                C.upsert_tokens(conn, m["conditionId"], C.token_rows(m, mt, teams))
                markets_upserted += 1
                _metric(conn, m, ts)
        # refresh status of tracked games that no longer show up in the moneyline listing (e.g. closed)
        for eid in tracked - set(by_event):
            ev = events.get(eid)
            if ev is not None:
                C.upsert_game(conn, C.game_row(ev, sport, source="discover"), ts)
                if ev.get("gameId") is not None:
                    game_by_gid[str(ev["gameId"])] = (eid, C.teams_of(ev, sport))

    # game-level totals/spreads above the volume floor
    lines = gamma.markets_keyset({
        "tag_id": tag, "closed": "false", "sports_market_types": ["totals", "spreads"],
        "volume_num_min": cfg.line_min_volume,
    }, client)
    known_gids = dict(game_by_gid)
    for r in conn.execute("SELECT game_key, polymarket_game_id FROM games WHERE sport=? AND polymarket_game_id IS NOT NULL "
                          "AND COALESCE(status,'') NOT IN ('ended','cancelled')", (sport,)):
        if r["polymarket_game_id"] not in known_gids:
            known_gids[r["polymarket_game_id"]] = (r["game_key"], None)
    lines_upserted = 0
    with dbmod.tx(conn):
        for m in lines:
            mt = C.market_type_of(m)
            ev = (m.get("events") or [{}])[0]
            gid = str(ev.get("gameId")) if ev.get("gameId") is not None else None
            if mt not in ("total", "spread") or gid not in known_gids or C.market_volume(m) < cfg.line_min_volume:
                continue
            game_key, teams = known_gids[gid]
            if teams is None:
                teams = _teams_from_db(conn, game_key, sport)
            C.upsert_market(conn, C.market_row(m, game_key=game_key, market_type=mt), ts)
            C.upsert_tokens(conn, m["conditionId"], C.token_rows(m, mt, teams))
            _metric(conn, m, ts)
            lines_upserted += 1
    return {"moneyline_markets_listed": len(ml), "events": len(by_event), "games": games_upserted,
            "skipped_by_league_or_volume": skipped_filter, "markets": markets_upserted,
            "lines_listed": len(lines), "lines": lines_upserted}


def _teams_from_db(conn, game_key: str, sport: str) -> C.Teams:
    r = conn.execute("SELECT title, home_team, away_team FROM games WHERE game_key=?", (game_key,)).fetchone()
    ev = {"title": r["title"] if r else None, "teams": []}
    if r and r["home_team"]:
        ev["teams"].append({"name": r["home_team"], "ordering": "home"})
    if r and r["away_team"]:
        ev["teams"].append({"name": r["away_team"], "ordering": "away"})
    return C.teams_of(ev, sport)


def _metric(conn, m: dict, ts: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO market_metrics(condition_id, ts, volume, liquidity) VALUES(?,?,?,?)",
        (m["conditionId"], C.minute(ts), C.market_volume(m), C._float(m.get("liquidityNum", m.get("liquidity")))),
    )


def refresh_closed(conn, client: Client, ts: int) -> dict:
    """Mark tracked markets closed/resolved; mark games ended when all their markets closed."""
    rows = conn.execute(
        """SELECT m.condition_id FROM markets m JOIN games g ON g.game_key = m.game_key
           WHERE (m.closed = 0 OR m.resolved_outcome_index IS NULL)
             AND g.start_time <= ? AND g.start_time >= ?""",
        (ts, ts - 14 * 86400)).fetchall()
    cids = [r[0] for r in rows]
    if not cids:
        return {"checked": 0, "closed": 0, "resolved": 0}
    closed = gamma.markets_by_condition_ids(cids, closed=True, client=client)
    n_closed = n_resolved = 0
    need_fallback: list[str] = []
    with dbmod.tx(conn):
        for m in closed:
            cid = m.get("conditionId")
            if not cid:
                continue
            winner = C.gamma_winner(m)
            conn.execute("UPDATE markets SET closed=1, updated_at=? WHERE condition_id=?", (ts, cid))
            n_closed += 1
            if winner is not None:
                n_resolved += _set_resolution(conn, cid, winner,
                                              gamma.parse_time(m.get("closedTime")) or gamma.parse_time(m.get("umaEndDate")),
                                              "gamma_outcome_prices", ts)
            else:
                need_fallback.append(cid)
    if need_fallback:
        try:
            res = data_api.resolutions(need_fallback, client)
        except Exception as exc:
            log.warning("resolutions fallback failed: %r", exc)
            res = {}
        with dbmod.tx(conn):
            for cid, row in res.items():
                w = data_api.winner_from_resolution(row)
                if w is not None:
                    n_resolved += _set_resolution(conn, cid, w, gamma.parse_time(row.get("resolved_at"))
                                                  or gamma.parse_time(row.get("last_update_timestamp")), "data_api_v2", ts)
    with dbmod.tx(conn):
        conn.execute(
            """UPDATE games SET status='ended', ended_at=COALESCE(ended_at, ?), updated_at=?
               WHERE COALESCE(status,'') NOT IN ('ended','cancelled')
                 AND EXISTS (SELECT 1 FROM markets m WHERE m.game_key = games.game_key)
                 AND NOT EXISTS (SELECT 1 FROM markets m WHERE m.game_key = games.game_key AND m.closed = 0)""",
            (ts, ts))
    return {"checked": len(cids), "closed": n_closed, "resolved": n_resolved}


def _set_resolution(conn, cid: str, winner: int, resolved_at: int | None, source: str, ts: int) -> int:
    cur = conn.execute(
        """UPDATE markets SET resolved_outcome_index=?, resolved_at=COALESCE(resolved_at, ?),
                  resolution_source=COALESCE(resolution_source, ?), closed=1, updated_at=?
           WHERE condition_id=? AND resolved_outcome_index IS NULL""",
        (winner, resolved_at, source, ts, cid))
    return cur.rowcount


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab discover", description=__doc__.split("\n")[0])
    ap.add_argument("--sports", help="comma list (default: all configured)")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from polylab.settings import paths
    p = paths()
    conn = dbmod.core(p)
    cfg = C.load_config()
    if args.sports:
        from dataclasses import replace
        cfg = replace(cfg, sports=tuple(s for s in args.sports.split(",") if s in C.SPORT_TAGS))
    run_id = C.job_start(conn, "discover")
    t0 = time.time()
    try:
        summary = discover(conn, cfg)
        ok = not any("error" in v for v in summary["sports"].values())
    except Exception as exc:
        C.job_finish(conn, run_id, False, {"error": repr(exc)})
        raise
    summary["secs"] = round(time.time() - t0, 2)
    summary["http_calls"] = default_client().calls
    C.job_finish(conn, run_id, ok, summary)
    print(json.dumps(summary, indent=None if not args.json else 2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
