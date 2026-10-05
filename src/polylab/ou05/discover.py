"""`polylab ou05 discover` (hourly) — register every soccer Total 0.5 market from its creation, mark closings.

Sources (probed 2026-10-05 from the Mac mini, docs/research/ou05-overround-study.md):
1. Gamma `GET /markets/keyset?tag_id=100350&closed=false&sports_market_types=totals` walked by cursor
   (~84 pages / ~8.3k markets / ~30 s: the `line` filter is NOT honoured server-side, so lines are filtered here).
   Keep `line == 0.5` with outcomes Over/Under, every soccer league. `gameStartTime` is refreshed on every run
   (postponed games move it; markets can be listed ~97 days ahead).
2. Closing: registered open markets missing from the open listing -> Gamma `/markets/keyset?condition_ids=..
   &closed=true`; winner from `outcomePrices` + `umaResolutionStatus == resolved` (common.gamma_winner), else
   Data API `/v2/resolutions` payouts. The winner index is mapped through the `outcomes` labels to Over/Under.
   Unresolved closed markets are retried for 14 days.
3. Final score: Gamma `/events/keyset?id=<main event>` `score` (title order == home first for soccer).
Writes data/ou05/registry.db only (ou05_markets upsert, ou05_metrics volume/liquidity per run).
"""

from __future__ import annotations

import json
import logging
import time

from polylab import db as dbmod
from polylab.api import data_api, gamma
from polylab.api.http import Client, default_client
from polylab.collector import common as C
from polylab.ou05 import store

log = logging.getLogger("polylab.ou05.discover")

SOCCER_TAG = C.SPORT_TAGS["soccer"]
LINE = 0.5
STALE_OPEN_S = 24 * 3600          # poll stops this long after kickoff even if the market is still open
RESOLVE_RETRY_S = 14 * 86400


def is_ou05(m: dict) -> bool:
    if m.get("sportsMarketType") != "totals" or C._float(m.get("line")) != LINE:
        return False
    labels = [str(x).lower() for x in gamma.json_list(m.get("outcomes"))]
    return sorted(labels) == ["over", "under"] and len(gamma.json_list(m.get("clobTokenIds"))) == 2


def side_tokens(m: dict) -> tuple[str, str]:
    """(over_token, under_token) aligned by the `outcomes` labels, never by position."""
    toks = [str(t) for t in gamma.json_list(m.get("clobTokenIds"))]
    labels = [str(x).lower() for x in gamma.json_list(m.get("outcomes"))]
    return toks[labels.index("over")], toks[labels.index("under")]


def winner_is_over(m: dict, winner_index: int | None) -> int | None:
    if winner_index is None:
        return None
    labels = [str(x).lower() for x in gamma.json_list(m.get("outcomes"))]
    if winner_index >= len(labels) or labels[winner_index] not in ("over", "under"):
        return None
    return 1 if labels[winner_index] == "over" else 0


def _teams(m: dict, ev: dict) -> tuple[str | None, str | None]:
    home, away = C.split_title(ev.get("title"))
    if home is None:
        home, away = C.split_title((m.get("question") or "").split(":")[0])
    return home, away


def market_row(m: dict, *, ts: int, source: str) -> dict:
    ev = (m.get("events") or [{}])[0]
    over, under = side_tokens(m)
    home, away = _teams(m, ev)
    parent = ev.get("parentEventId")
    closed = 1 if m.get("closed") else 0
    winner = winner_is_over(m, C.gamma_winner(m)) if closed else None
    return {
        "condition_id": m["conditionId"],
        "market_id": str(m["id"]) if m.get("id") is not None else None,
        "game_key": str(parent) if parent is not None else (str(ev["id"]) if ev.get("id") is not None else None),
        "event_id": str(ev["id"]) if ev.get("id") is not None else None,
        "league": C.league_code(ev, m),
        "home": home, "away": away,
        "question": m.get("question"), "slug": m.get("slug"),
        "over_token": over, "under_token": under,
        "created_at": gamma.parse_time(m.get("createdAt")),
        "game_start": gamma.parse_time(m.get("gameStartTime")) or gamma.parse_time(ev.get("startTime")),
        "end_date": gamma.parse_time(m.get("endDate")),
        "closed": closed,
        "closed_at": gamma.parse_time(m.get("closedTime")) if closed else None,
        "resolved_over": winner,
        "resolution_source": "gamma_outcome_prices" if winner is not None else None,
        "volume": C.market_volume(m),
        "liquidity": C._float(m.get("liquidityNum", m.get("liquidity"))),
        "ts": ts, "source": source,
    }


UPSERT = """
INSERT INTO ou05_markets(condition_id, market_id, game_key, event_id, league, home, away, question, slug,
                         over_token, under_token, created_at, game_start, end_date, closed, closed_at,
                         resolved_over, resolution_source, volume, liquidity, first_seen, updated_at, source)
VALUES(:condition_id, :market_id, :game_key, :event_id, :league, :home, :away, :question, :slug,
       :over_token, :under_token, :created_at, :game_start, :end_date, :closed, :closed_at,
       :resolved_over, :resolution_source, :volume, :liquidity, :ts, :ts, :source)
ON CONFLICT(condition_id) DO UPDATE SET
    market_id=COALESCE(excluded.market_id, ou05_markets.market_id),
    game_key=COALESCE(excluded.game_key, ou05_markets.game_key),
    event_id=COALESCE(excluded.event_id, ou05_markets.event_id),
    league=COALESCE(excluded.league, ou05_markets.league),
    home=COALESCE(excluded.home, ou05_markets.home),
    away=COALESCE(excluded.away, ou05_markets.away),
    question=COALESCE(excluded.question, ou05_markets.question),
    slug=COALESCE(excluded.slug, ou05_markets.slug),
    created_at=COALESCE(ou05_markets.created_at, excluded.created_at),
    game_start=COALESCE(excluded.game_start, ou05_markets.game_start),
    end_date=COALESCE(excluded.end_date, ou05_markets.end_date),
    closed=MAX(ou05_markets.closed, excluded.closed),
    closed_at=COALESCE(ou05_markets.closed_at, excluded.closed_at),
    resolved_over=COALESCE(ou05_markets.resolved_over, excluded.resolved_over),
    resolution_source=COALESCE(ou05_markets.resolution_source, excluded.resolution_source),
    volume=COALESCE(excluded.volume, ou05_markets.volume),
    liquidity=COALESCE(excluded.liquidity, ou05_markets.liquidity),
    updated_at=excluded.updated_at
"""


def upsert(conn, row: dict) -> None:
    conn.execute(UPSERT, row)


def discover(conn, client: Client | None = None, ts: int | None = None) -> dict:
    client = client or default_client()
    ts = ts or C.now()
    t0 = time.time()
    listed = 0
    open_rows: dict[str, dict] = {}
    for rows, _ in gamma.markets_keyset_pages(
            {"tag_id": SOCCER_TAG, "closed": "false", "sports_market_types": "totals"}, client):
        listed += len(rows)
        for m in rows:
            if m.get("conditionId") and is_ou05(m) and not m.get("closed"):
                open_rows[m["conditionId"]] = market_row(m, ts=ts, source="discover")
    known = {r[0] for r in conn.execute("SELECT condition_id FROM ou05_markets")}
    with dbmod.tx(conn):
        for row in open_rows.values():
            upsert(conn, row)
            conn.execute("INSERT OR REPLACE INTO ou05_metrics(condition_id, ts, volume, liquidity) VALUES(?,?,?,?)",
                         (row["condition_id"], C.minute(ts), row["volume"], row["liquidity"]))
    out = {"listed_totals": listed, "open_ou05": len(open_rows),
           "new": len(set(open_rows) - known), "list_secs": round(time.time() - t0, 1)}
    out["closing"] = refresh_closed(conn, set(open_rows), client, ts)
    stale = conn.execute("SELECT condition_id, game_start FROM ou05_markets WHERE closed=0 AND game_start < ?",
                         (ts - STALE_OPEN_S,)).fetchall()
    if stale:
        with dbmod.tx(conn):
            store.quality(conn, "ou05_stale_open", None, count=len(stale),
                          oldest_start=min(r["game_start"] for r in stale), sample=[r[0] for r in stale[:5]])
    out["stale_open"] = len(stale)
    out["open_registered"] = conn.execute("SELECT COUNT(*) FROM ou05_markets WHERE closed=0").fetchone()[0]
    out["secs"] = round(time.time() - t0, 1)
    return out


def refresh_closed(conn, open_ids: set[str], client: Client, ts: int) -> dict:
    """Registered markets that left the open listing, plus closed-but-unresolved ones (<= 14 days)."""
    gone = [r[0] for r in conn.execute("SELECT condition_id FROM ou05_markets WHERE closed=0")
            if r[0] not in open_ids]
    unresolved = [r[0] for r in conn.execute(
        "SELECT condition_id FROM ou05_markets WHERE closed=1 AND resolved_over IS NULL "
        "AND COALESCE(closed_at, game_start, 0) >= ?", (ts - RESOLVE_RETRY_S,))]
    cids = list(dict.fromkeys(gone + unresolved))
    out = {"checked": len(cids), "closed": 0, "resolved": 0, "not_found": 0, "scores": 0}
    if not cids:
        return out
    closed = {m["conditionId"]: m for m in gamma.markets_by_condition_ids(cids, closed=True, client=client)
              if m.get("conditionId")}
    need_fallback: list[str] = []
    payload: dict[str, dict] = {}
    with dbmod.tx(conn):
        for cid in cids:
            m = closed.get(cid)
            if m is None:
                out["not_found"] += 1
                continue
            payload[cid] = m
            cur = conn.execute("UPDATE ou05_markets SET closed=1, closed_at=COALESCE(closed_at, ?), updated_at=? "
                               "WHERE condition_id=? AND closed=0",
                               (gamma.parse_time(m.get("closedTime")) or ts, ts, cid))
            out["closed"] += cur.rowcount
            # final volume/liquidity: most O/U 0.5 volume trades near and during the game, after the last open listing
            vol, liq = C.market_volume(m), C._float(m.get("liquidityNum", m.get("liquidity")))
            conn.execute("UPDATE ou05_markets SET volume=?, liquidity=COALESCE(?, liquidity) WHERE condition_id=?",
                         (vol, liq, cid))
            conn.execute("INSERT OR REPLACE INTO ou05_metrics(condition_id, ts, volume, liquidity) VALUES(?,?,?,?)",
                         (cid, C.minute(ts), vol, liq))
            w = winner_is_over(m, C.gamma_winner(m))
            if w is None:
                need_fallback.append(cid)
            else:
                out["resolved"] += _set_resolved(conn, cid, w, "gamma_outcome_prices", ts)
    if need_fallback:
        try:
            res = data_api.resolutions(need_fallback, client)
        except Exception as exc:  # noqa: BLE001 — resolution can wait for the next hourly run
            log.warning("resolutions fallback failed: %r", exc)
            res = {}
        with dbmod.tx(conn):
            for cid, row in res.items():
                w = winner_is_over(payload.get(cid, {}), data_api.winner_from_resolution(row))
                if w is not None:
                    out["resolved"] += _set_resolved(conn, cid, w, "data_api_v2", ts)
    out["scores"] = fill_scores(conn, client, ts)
    return out


def _set_resolved(conn, cid: str, over: int, source: str, ts: int) -> int:
    return conn.execute("UPDATE ou05_markets SET resolved_over=?, resolution_source=?, updated_at=? "
                        "WHERE condition_id=? AND resolved_over IS NULL", (over, source, ts, cid)).rowcount


def fill_scores(conn, client: Client, ts: int, limit: int = 500) -> int:
    """Final score for closed markets without one (main event `score`, home first for soccer)."""
    rows = conn.execute("SELECT DISTINCT game_key FROM ou05_markets WHERE closed=1 AND final_score IS NULL "
                        "AND game_key IS NOT NULL AND COALESCE(closed_at, game_start, 0) >= ? LIMIT ?",
                        (ts - RESOLVE_RETRY_S, limit)).fetchall()
    keys = [r[0] for r in rows]
    if not keys:
        return 0
    try:
        events = gamma.events_by_ids(keys, client)
    except Exception as exc:  # noqa: BLE001
        log.warning("score lookup failed: %r", exc)
        return 0
    n = 0
    with dbmod.tx(conn):
        for ev in events:
            if C.game_status(ev) != "ended":
                continue
            h, a = C.parse_score(ev.get("score"), C.teams_of(ev, "soccer").first_is_home)
            if h is None:
                continue
            n += conn.execute("UPDATE ou05_markets SET final_score=?, home_score=?, away_score=?, updated_at=? "
                              "WHERE game_key=? AND final_score IS NULL", (f"{h}-{a}", h, a, ts, str(ev["id"]))).rowcount
    return n


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="polylab ou05 discover", description=__doc__.split("\n")[0])
    ap.parse_args(argv)
    from polylab.settings import paths
    conn = store.registry(paths())
    run_id = store.job_start(conn, "ou05-discover")
    try:
        out = discover(conn)
    except Exception as exc:
        store.job_finish(conn, run_id, False, {"error": repr(exc)[:500]})
        raise
    out["http_calls"] = default_client().calls
    store.job_finish(conn, run_id, True, out)
    conn.close()
    print(json.dumps(out))
    return 0
