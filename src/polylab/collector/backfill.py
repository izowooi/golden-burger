"""`polylab backfill [--recent] [--historical --budget-minutes N] [--since 2026-02-01] [--game KEY]`

--recent (hourly): games that ended in the last 48 h whose markets are not yet backfilled:
  * CLOB `POST /batch-prices-history` fidelity=1 for every tracked token, start-60m .. end+30m
    -> price_bars(source='history', minute bucket, last point in the minute wins)
  * Data API `GET /v2/trades?condition_id&taker_only=true` (cursor walk) -> public_trades
    (TAKER rows only: one row per fill, so sums are volume; maker-side rows are not stored)
  * Data API `GET /v2/oi` -> market_metrics.open_interest (post-game snapshot)
  * resolution: Gamma closed markets (outcomePrices + umaResolutionStatus) else `/v2/resolutions` payouts
  * validation -> quality_events: live poll_mid vs history > 5c (per token: count + max), poll_mid gaps > 3 min
    during play, history gaps > 3 min during play.
  backfill_status(condition_id, kind in prices|trades|oi|resolution) records progress; reruns skip done work.
  * extra markets (common.extra_market_included, FINAL volume): Gamma `/events/keyset?game_id=` after the game
    adds soccer totals/btts/team_to_score (major competitions only) and US totals/spreads that crossed the
    floor too late for discover. Extras get 1-minute prices for outcome 0 only (Over / Yes / first team; the
    complement is ~1-p) and no public_trades walk (trades stay moneyline/draw: compact).
--game KEY: the same for one game regardless of age/status.
--historical: closed games since --since (default 2026-02-01), resumable and budgeted:
  phase A enumerates Gamma `GET /markets/keyset?tag_id&closed=true&sports_market_types=moneyline` per sport
    (cursor + the exact query params checkpointed; a param change restarts the walk) into the staging table
    `collector_hist_markets` (created here; core.db, additive);
  phase B processes staged events newest first: Gamma `/events/keyset?id=` for teams/final score, league/volume
    filter (same rules as discover), upsert games/markets/tokens (source='history_backfill'), then 1-minute CLOB
    prices for moneyline tokens ONLY (soccer: the 3 Yes tokens; US: both team tokens).
  phase C (extras, at most EXTRAS_BUDGET_SHARE of the budget): Gamma `GET /markets/keyset?tag_id&closed=true
    &sports_market_types=<extra types>&volume_num_min=<floor>` walked with its own checkpoint
    (backfill.hist.extras.enum.<sport>) into `collector_hist_extras`, pre-filtered to major soccer competitions;
    rows are attached once their game exists (parentEventId / event id / gameId) and get 1-minute prices for
    outcome 0 only. Rows whose game is not stored yet wait for a later run.
  Historical game_states are NOT available: there is no REST play-by-play/score timeline and the sports WS has
  no replay (api-sources.md), so only final score/finish time are stored for historical games.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone

from polylab import db as dbmod
from polylab.api import clob_public, data_api, gamma
from polylab.api.http import Client, default_client
from polylab.collector import common as C

log = logging.getLogger("polylab.backfill")

PRE_S = 3600
POST_S = 1800
MISMATCH = 0.05
GAP_S = 180
RECENT_S = 48 * 3600

EXTRAS_BUDGET_SHARE = 0.25

HIST_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS collector_hist_markets (
        condition_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        sport TEXT NOT NULL,
        game_start INTEGER,
        payload TEXT NOT NULL,              -- trimmed Gamma market json
        processed INTEGER NOT NULL DEFAULT 0, -- 0 todo | 1 stored | 2 filtered out | 3 failed
        updated_at INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS collector_hist_markets_todo ON collector_hist_markets(processed, sport, game_start)",
    """
    CREATE TABLE IF NOT EXISTS collector_hist_extras (
        condition_id TEXT PRIMARY KEY,
        sport TEXT NOT NULL,
        parent_event_id TEXT,               -- child "More Markets" event's parent = main event id = game_key
        event_id TEXT,
        game_id TEXT,                       -- sports gameId (missing on ~half of the closed soccer child events)
        game_start INTEGER,
        payload TEXT NOT NULL,              -- trimmed Gamma market json ('' once processed)
        processed INTEGER NOT NULL DEFAULT 0, -- 0 todo | 1 stored | 3 failed
        updated_at INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS collector_hist_extras_todo ON collector_hist_extras(processed, sport, game_start)",
)

KEEP_MARKET_KEYS = ("conditionId", "id", "question", "slug", "groupItemTitle", "outcomes", "clobTokenIds",
                    "outcomePrices", "volumeNum", "volume", "sportsMarketType", "gameStartTime", "closedTime",
                    "umaEndDate", "umaResolutionStatus", "closed", "negRisk", "createdAt", "endDate", "feeType",
                    "feesEnabled", "feeSchedule", "makerBaseFee", "takerBaseFee", "liquidityNum", "line")
KEEP_EVENT_KEYS = ("id", "title", "slug", "ticker", "gameId", "startTime", "seriesSlug", "parentEventId")


def ensure_hist_schema(conn) -> None:
    for stmt in HIST_SCHEMA:
        conn.execute(stmt)
    conn.commit()


def trade_uid(t: dict) -> str:
    key = "|".join(str(t.get(k, "")) for k in ("transaction_hash", "token_id", "side", "size", "price", "timestamp",
                                                 "proxy_wallet"))
    return hashlib.sha1(key.encode()).hexdigest()


def _status(conn, cid: str, kind: str, status: str, rows: int | None = None, detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO backfill_status(condition_id, kind, status, rows, detail, updated_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(condition_id, kind) DO UPDATE SET status=excluded.status, rows=excluded.rows, "
        "detail=excluded.detail, updated_at=excluded.updated_at", (cid, kind, status, rows, detail, C.now()))


def history_rows(points: list[tuple[int, float]], token: str) -> list[tuple]:
    """Bucket (t, p) points to minutes; the last point inside a minute wins. received_at left NULL (compact)."""
    bucket: dict[int, float] = {}
    for t, p in sorted(points):
        bucket[C.minute(t)] = p
    return [(token, m, "history", p, None) for m, p in bucket.items()]


def game_window(g, ts: int) -> tuple[int, int, int]:
    start = int(g["start_time"])
    end = int(g["ended_at"] or start + C.NOMINAL_GAME_SECONDS.get(g["sport"], 3 * 3600))
    return start - PRE_S, min(end + POST_S, ts), end


# ------------------------------------------------------------------ recent

def recent_games(conn, ts: int) -> list:
    rows = conn.execute(
        """SELECT g.* FROM games g
           WHERE g.start_time IS NOT NULL AND g.start_time >= :lo AND g.start_time <= :now
             AND (g.status = 'ended' OR g.ended_at IS NOT NULL
                  OR NOT EXISTS (SELECT 1 FROM markets m WHERE m.game_key=g.game_key AND m.closed=0))
             AND EXISTS (SELECT 1 FROM markets m WHERE m.game_key = g.game_key AND NOT EXISTS (
                  SELECT 1 FROM backfill_status b WHERE b.condition_id=m.condition_id AND b.kind='prices'
                     AND b.status IN ('done','empty')))
           ORDER BY g.start_time""", {"lo": ts - RECENT_S - 6 * 3600, "now": ts}).fetchall()
    out = []
    for g in rows:
        end = g["ended_at"] or (g["start_time"] + C.MAX_GAME_SECONDS.get(g["sport"], 4 * 3600))
        if end >= ts - RECENT_S and (g["status"] == "ended" or g["ended_at"] or end <= ts):
            out.append(g)
    return out


def add_extra_markets(conn, g, client: Client, cfg: C.CollectorConfig, ts: int) -> int:
    """Post-game: store the game's extra markets whose FINAL volume passed the floor (discover only saw the
    volume at the time it ran). Soccer only for major competitions."""
    sport = g["sport"]
    if sport == "soccer" and (g["league"] or "").lower() not in cfg.soccer_leagues:
        return 0
    if not g["polymarket_game_id"]:
        return 0
    events = gamma.events_keyset({"game_id": g["polymarket_game_id"]}, client)
    main = next((e for e in events if str(e.get("id")) == g["game_key"]), None)
    teams = C.teams_of(main, sport) if main else None
    n = 0
    with dbmod.tx(conn):
        for ev in events:
            for m in ev.get("markets") or []:
                mt = C.market_type_of(m, sport)
                if mt not in C.EXTRA_MARKET_TYPES or not m.get("conditionId"):
                    continue
                m = {**m, "events": [ev]}
                if C.extra_game_key(m, {g["game_key"]}, {}) != g["game_key"] or not C.extra_market_included(m, mt, sport, cfg):
                    continue
                if teams is None:
                    from polylab.collector.discover import _teams_from_db
                    teams = _teams_from_db(conn, g["game_key"], sport)
                C.upsert_market(conn, C.market_row(m, game_key=g["game_key"], market_type=mt), ts)
                C.upsert_tokens(conn, m["conditionId"], C.token_rows(m, mt, teams))
                n += 1
    return n


def backfill_game(conn, g, client: Client, ts: int, q: C.QualityLog, *, trades: bool = True,
                  cfg: C.CollectorConfig | None = None) -> dict:
    gk = g["game_key"]
    lo, hi, end = game_window(g, ts)
    complete = ts >= end + POST_S          # window still open -> 'partial', finished on a later run
    out = {"game": gk, "bars": 0, "trades": 0, "extras": 0}
    if complete:
        try:
            out["extras"] = add_extra_markets(conn, g, client, cfg or C.load_config(), ts)
        except Exception as exc:
            log.warning("extras %s: %r", gk, exc)
            q.add("extras_failed", gk, None, error=repr(exc)[:300])
    markets = conn.execute("SELECT * FROM markets WHERE game_key=?", (gk,)).fetchall()
    # extra markets: outcome 0 only (Over / Yes / first team) — the complement is ~1-p, keep storage compact
    toks = conn.execute(
        "SELECT t.token_id, t.condition_id FROM tokens t JOIN markets m ON m.condition_id=t.condition_id "
        "WHERE m.game_key=? AND (m.market_type IN ('moneyline','draw') OR t.outcome_index = 0)", (gk,)).fetchall()
    hist = clob_public.batch_prices_history([t["token_id"] for t in toks], lo, hi, fidelity=1, client=client)
    per_cid: dict[str, int] = {}
    out["compared"] = out["mismatched"] = 0
    with dbmod.tx(conn):
        for t in toks:
            rows = history_rows(hist.get(t["token_id"], []), t["token_id"])
            out["bars"] += C.write_price_bars(conn, rows)
            per_cid[t["condition_id"]] = per_cid.get(t["condition_id"], 0) + len(rows)
            n_cmp, n_bad = validate_token(conn, t["token_id"], rows, g["start_time"], min(end, hi), q)
            out["compared"] += n_cmp
            out["mismatched"] += n_bad
        for m in markets:
            n = per_cid.get(m["condition_id"], 0)
            _status(conn, m["condition_id"], "prices", ("done" if n else "empty") if complete else "partial", n,
                    f"{lo}-{hi}")
    cids = [m["condition_id"] for m in markets]
    trade_cids = [m["condition_id"] for m in markets if m["market_type"] in ("moneyline", "draw")]
    try:
        oi = data_api.open_interest(cids, client)
        with dbmod.tx(conn):
            for cid, v in oi.items():
                conn.execute("INSERT INTO market_metrics(condition_id, ts, open_interest) VALUES(?,?,?) "
                             "ON CONFLICT(condition_id, ts) DO UPDATE SET open_interest=excluded.open_interest",
                             (cid, C.minute(ts), v))
                _status(conn, cid, "oi", "done", 1)
    except Exception as exc:
        log.warning("oi %s: %r", gk, exc)
    if trades:
        for cid in trade_cids:
            done = conn.execute("SELECT status FROM backfill_status WHERE condition_id=? AND kind='trades'",
                                (cid,)).fetchone()
            if done and done[0] == "done":
                continue
            try:
                out["trades"] += store_trades(conn, cid, client, complete=complete)
            except Exception as exc:
                log.warning("trades %s: %r", cid, exc)
                with dbmod.tx(conn):
                    _status(conn, cid, "trades", "failed", None, repr(exc)[:300])
    out["resolved"] = resolve_markets(conn, [m["condition_id"] for m in markets if m["resolved_outcome_index"] is None],
                                      client, ts)
    return out


def store_trades(conn, cid: str, client: Client, complete: bool = True) -> int:
    n = 0
    for page in data_api.trades(cid, taker_only=True, client=client):
        rows = []
        for t in page:
            try:
                size, price = float(t["size"]), float(t["price"])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append((trade_uid(t), t.get("condition_id") or cid, t.get("token_id"), int(t.get("timestamp") or 0),
                         t.get("side"), price, size, round(size * price, 6), t.get("proxy_wallet"),
                         t.get("transaction_hash"), t.get("outcome_index")))
        with dbmod.tx(conn):
            conn.executemany("INSERT OR IGNORE INTO public_trades(uid, condition_id, token_id, ts, side, price, size, usd, "
                             "wallet, tx_hash, outcome_index) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
        n += len(rows)
    with dbmod.tx(conn):
        _status(conn, cid, "trades", ("done" if n else "empty") if complete else "partial", n, "taker_only")
    return n


def resolve_markets(conn, cids: list[str], client: Client, ts: int) -> int:
    if not cids:
        return 0
    from polylab.collector.discover import _set_resolution
    n = 0
    closed = gamma.markets_by_condition_ids(cids, closed=True, client=client)
    missing = set(cids)
    with dbmod.tx(conn):
        for m in closed:
            cid = m.get("conditionId")
            w = C.gamma_winner(m)
            conn.execute("UPDATE markets SET closed=1 WHERE condition_id=?", (cid,))
            if w is not None:
                n += _set_resolution(conn, cid, w, gamma.parse_time(m.get("closedTime")), "gamma_outcome_prices", ts)
                _status(conn, cid, "resolution", "done", 1, "gamma")
                missing.discard(cid)
    if missing:
        try:
            res = data_api.resolutions(sorted(missing), client)
        except Exception as exc:
            log.warning("resolutions fallback failed: %r", exc)
            res = {}
        with dbmod.tx(conn):
            for cid in missing:
                row = res.get(cid)
                w = data_api.winner_from_resolution(row) if row else None
                if w is not None:
                    n += _set_resolution(conn, cid, w, gamma.parse_time(row.get("resolved_at")), "data_api_v2", ts)
                    _status(conn, cid, "resolution", "done", 1, "data_api_v2")
                else:
                    _status(conn, cid, "resolution", "partial", 0, (row or {}).get("status") or "not resolved")
    return n


def validate_token(conn, token: str, hist_rows: list[tuple], start: int, end: int, q: C.QualityLog) -> tuple[int, int]:
    """Compare live poll_mid with history at the same minute; flag gaps > 3 min during play (start..end).
    Returns (minutes compared, minutes differing by > 5c)."""
    hist = {r[1]: r[3] for r in hist_rows}
    live = conn.execute("SELECT ts, price FROM price_bars WHERE token_id=? AND source='poll_mid' AND ts BETWEEN ? AND ?",
                        (token, start - PRE_S, end + POST_S)).fetchall()
    compared = bad = 0
    for r in live:
        h = hist.get(r["ts"])
        if h is None:
            continue
        compared += 1
        if abs(h - r["price"]) > MISMATCH:
            bad += 1
            q.add("live_history_mismatch", token, abs(h - r["price"]), first_ts=r["ts"])
    for kind, series in (("live_gap", [r["ts"] for r in live]), ("history_gap", sorted(hist))):
        inplay = [t for t in series if start <= t <= end]
        if not inplay:
            continue
        for a, b in zip([start] + inplay, inplay + [end]):
            if b - a > GAP_S:
                q.add(kind, token, b - a, first_ts=a)
    return compared, bad


def run_recent(conn, client: Client, ts: int, game: str | None = None, budget_s: float = 3000,
               trades: bool = True, cfg: C.CollectorConfig | None = None) -> dict:
    cfg = cfg or C.load_config()
    q = C.QualityLog()
    t0 = time.time()
    games = ([conn.execute("SELECT * FROM games WHERE game_key=?", (game,)).fetchone()] if game
             else recent_games(conn, ts))
    games = [g for g in games if g is not None]
    done = []
    for g in games:
        if time.time() - t0 > budget_s:
            break
        try:
            done.append(backfill_game(conn, g, client, ts, q, trades=trades, cfg=cfg))
        except Exception as exc:
            log.exception("backfill %s failed", g["game_key"])
            q.add("backfill_failed", g["game_key"], None, error=repr(exc)[:300])
    with dbmod.tx(conn):
        nq = q.flush(conn)
    return {"games_candidates": len(games), "games_done": len(done), "bars": sum(d["bars"] for d in done),
            "trades": sum(d["trades"] for d in done), "extras": sum(d.get("extras", 0) for d in done),
            "resolved": sum(d.get("resolved", 0) for d in done),
            "live_vs_history_compared": sum(d.get("compared", 0) for d in done),
            "live_vs_history_mismatched": sum(d.get("mismatched", 0) for d in done),
            "quality": nq, "secs": round(time.time() - t0, 1)}


# ------------------------------------------------------------------ historical

def enum_params(sport: str, since: int) -> dict:
    """Fixed query (no now-derived values) so the keyset cursor stays valid across runs."""
    return {"tag_id": C.SPORT_TAGS[sport], "closed": "true", "sports_market_types": "moneyline",
            "start_date_min": gamma.iso(since - 30 * 86400), "limit": 100}


def trim_market(m: dict) -> dict:
    out = {k: m.get(k) for k in KEEP_MARKET_KEYS if m.get(k) is not None}
    ev = (m.get("events") or [{}])[0]
    out["events"] = [{k: ev.get(k) for k in KEEP_EVENT_KEYS if ev.get(k) is not None}]
    return out


def enumerate_sport(conn, sport: str, since: int, client: Client, deadline: float) -> dict:
    name = f"backfill.hist.enum.{sport}"
    params = enum_params(sport, since)
    state = json.loads(dbmod.get_checkpoint(conn, name) or "{}")
    if state.get("params") != params:
        state = {"params": params, "cursor": None, "done": False, "pages": 0, "staged": 0}
    if state.get("done"):
        return state
    for rows, cursor in gamma.markets_keyset_pages(params, client, start_cursor=state.get("cursor")):
        with dbmod.tx(conn):
            for m in rows:
                ev = (m.get("events") or [{}])[0]
                start = gamma.parse_time(m.get("gameStartTime")) or gamma.parse_time(ev.get("startTime"))
                if not m.get("conditionId") or ev.get("id") is None or start is None or start < since:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO collector_hist_markets(condition_id, event_id, sport, game_start, payload, "
                    "updated_at) VALUES(?,?,?,?,?,?)",
                    (m["conditionId"], str(ev["id"]), sport, start, json.dumps(trim_market(m), separators=(",", ":")),
                     C.now()))
                state["staged"] += cur.rowcount
            state["pages"] += 1
            state["cursor"] = cursor
            state["done"] = not cursor or not rows
            dbmod.set_checkpoint(conn, name, json.dumps(state))
        if state["done"] or time.time() > deadline:
            break
    return state


def _fetch_prices(job: tuple) -> tuple:
    gk, tokens, lo, hi, client = job
    try:
        return gk, clob_public.batch_prices_history(tokens, lo, hi, fidelity=1, client=client), None
    except Exception as exc:
        return gk, {}, exc


def process_events(conn, cfg: C.CollectorConfig, client: Client, ts: int, deadline: float, workers: int = 4,
                   batch: int = 40) -> dict:
    stats = {"events_stored": 0, "events_filtered": 0, "events_failed": 0, "bars": 0}
    ready = [s for s in cfg.sports
             if json.loads(dbmod.get_checkpoint(conn, f"backfill.hist.enum.{s}") or "{}").get("done")]
    if not ready:
        return stats
    marks = ",".join("?" * len(ready))
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        while time.time() < deadline:
            evs = conn.execute(
                f"""SELECT event_id, sport, MAX(game_start) AS gs FROM collector_hist_markets
                    WHERE processed=0 AND sport IN ({marks}) GROUP BY event_id, sport
                    ORDER BY gs DESC LIMIT ?""", (*ready, batch)).fetchall()
            if not evs:
                break
            markets_by_ev: dict[str, list[dict]] = {}
            for r in conn.execute(
                    f"SELECT event_id, payload FROM collector_hist_markets WHERE event_id IN "
                    f"({','.join('?' * len(evs))})", [e["event_id"] for e in evs]):
                markets_by_ev.setdefault(r["event_id"], []).append(json.loads(r["payload"]))
            try:
                details = {str(e["id"]): e for e in gamma.events_by_ids([e["event_id"] for e in evs], client)}
            except Exception as exc:
                log.warning("events_by_ids failed: %r", exc)
                details = {}
            jobs = []
            with dbmod.tx(conn):
                for e in evs:
                    eid, sport = e["event_id"], e["sport"]
                    ms = markets_by_ev.get(eid, [])
                    ev = details.get(eid) or {**ms[0]["events"][0], "closed": True, "ended": True}
                    league = C.league_code(ev, ms[0] if ms else None)
                    vol = sum(C.market_volume(m) for m in ms)
                    if sport == "soccer" and not C.soccer_game_included(league, vol, cfg):
                        conn.execute("UPDATE collector_hist_markets SET processed=2, payload='', updated_at=? WHERE event_id=?",
                                     (C.now(), eid))
                        stats["events_filtered"] += 1
                        continue
                    row = C.game_row(ev, sport, source="history_backfill",
                                     fallback_start=gamma.parse_time(ms[0].get("gameStartTime")) if ms else None)
                    if row["status"] != "ended":
                        row["status"] = "ended"
                    if row["ended_at"] is None:
                        row["ended_at"] = gamma.parse_time(ev.get("finishedTimestamp"))
                    C.upsert_game(conn, row, ts)
                    teams = C.teams_of(ev, sport)
                    want: list[str] = []
                    for m in ms:
                        mt = C.market_type_of(m, sport)
                        if mt is None:
                            continue
                        C.upsert_market(conn, C.market_row(m, game_key=eid, market_type=mt), ts)
                        trows = C.token_rows(m, mt, teams)
                        C.upsert_tokens(conn, m["conditionId"], trows)
                        done = conn.execute("SELECT 1 FROM backfill_status WHERE condition_id=? AND kind='prices' "
                                            "AND status IN ('done','empty')", (m["conditionId"],)).fetchone()
                        if done:
                            continue
                        is_yes_no = [str(x).lower() for x in gamma.json_list(m.get("outcomes"))] == ["yes", "no"]
                        want.extend(t["token_id"] for t in trows if not (is_yes_no and t["side"] == "no"))
                    if want and row["start_time"]:
                        end = row["ended_at"] or row["start_time"] + C.NOMINAL_GAME_SECONDS.get(sport, 3 * 3600)
                        jobs.append((eid, want, row["start_time"] - PRE_S, min(end + POST_S, ts), client))
                    else:
                        conn.execute("UPDATE collector_hist_markets SET processed=1, payload='', updated_at=? "
                                     "WHERE event_id=?", (C.now(), eid))
                        stats["events_stored"] += 1
            for gk, hist, err in pool.map(_fetch_prices, jobs):
                with dbmod.tx(conn):
                    if err is not None:
                        log.warning("prices %s: %r", gk, err)
                        conn.execute("UPDATE collector_hist_markets SET processed=3, updated_at=? WHERE event_id=?",
                                     (C.now(), gk))
                        stats["events_failed"] += 1
                        continue
                    per_cid: dict[str, int] = {}
                    for tok, pts in hist.items():
                        rows = history_rows(pts, tok)
                        stats["bars"] += C.write_price_bars(conn, rows)
                        cid = conn.execute("SELECT condition_id FROM tokens WHERE token_id=?", (tok,)).fetchone()
                        if cid:
                            per_cid[cid[0]] = per_cid.get(cid[0], 0) + len(rows)
                    for (cid,) in conn.execute("SELECT condition_id FROM markets WHERE game_key=? AND market_type IN "
                                               "('moneyline','draw')", (gk,)).fetchall():
                        n = per_cid.get(cid, 0)
                        _status(conn, cid, "prices", "done" if n else "empty", n, "historical moneyline")
                    conn.execute("UPDATE collector_hist_markets SET processed=1, payload='', updated_at=? "
                                 "WHERE event_id=?", (C.now(), gk))
                    stats["events_stored"] += 1
    return stats


def extras_enum_params(sport: str, since: int, cfg: C.CollectorConfig) -> dict:
    """Fixed query (own checkpoint, so the moneyline walk's cursor is untouched)."""
    return {"tag_id": C.SPORT_TAGS[sport], "closed": "true",
            "sports_market_types": list(C.extra_sports_market_types(sport)),
            "volume_num_min": C.extra_min_volume(sport, cfg),
            "start_date_min": gamma.iso(since - 30 * 86400), "limit": 100}


def enumerate_extras(conn, sport: str, since: int, cfg: C.CollectorConfig, client: Client, deadline: float) -> dict:
    name = f"backfill.hist.extras.enum.{sport}"
    params = extras_enum_params(sport, since, cfg)
    state = json.loads(dbmod.get_checkpoint(conn, name) or "{}")
    if state.get("params") != params:
        state = {"params": params, "cursor": None, "done": False, "pages": 0, "staged": 0}
    if state.get("done") or time.time() > deadline:
        return state
    for rows, cursor in gamma.markets_keyset_pages(params, client, start_cursor=state.get("cursor")):
        with dbmod.tx(conn):
            for m in rows:
                ev = (m.get("events") or [{}])[0]
                start = gamma.parse_time(m.get("gameStartTime")) or gamma.parse_time(ev.get("startTime"))
                if not m.get("conditionId") or start is None or start < since:
                    continue
                if sport == "soccer" and C.league_code(ev, m) not in cfg.soccer_leagues:
                    continue
                if not C.extra_market_included(m, C.market_type_of(m, sport), sport, cfg):
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO collector_hist_extras(condition_id, sport, parent_event_id, event_id, game_id, "
                    "game_start, payload, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (m["conditionId"], sport, _s(ev.get("parentEventId")), _s(ev.get("id")), _s(ev.get("gameId")),
                     start, json.dumps(trim_market(m), separators=(",", ":")), C.now()))
                state["staged"] += cur.rowcount
            state["pages"] += 1
            state["cursor"] = cursor
            state["done"] = not cursor or not rows
            dbmod.set_checkpoint(conn, name, json.dumps(state))
        if state["done"] or time.time() > deadline:
            break
    return state


def _s(v) -> str | None:
    return str(v) if v is not None else None


def process_extras(conn, cfg: C.CollectorConfig, client: Client, ts: int, deadline: float, workers: int = 4,
                   batch: int = 40) -> dict:
    """Attach staged extra markets to stored, ended games; 1-minute prices for outcome 0 only."""
    stats = {"extras_stored": 0, "extras_failed": 0, "extras_waiting": 0, "extras_bars": 0}
    marks = ",".join("?" * len(cfg.sports))
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        while time.time() < deadline:
            rows = conn.execute(
                f"""SELECT x.condition_id, x.sport, x.payload, g.game_key, g.start_time, g.ended_at, g.league
                    FROM collector_hist_extras x
                    JOIN games g ON g.game_key = COALESCE(
                        (SELECT game_key FROM games WHERE game_key = x.parent_event_id),
                        (SELECT game_key FROM games WHERE game_key = x.event_id),
                        (SELECT game_key FROM games WHERE polymarket_game_id = x.game_id AND x.game_id IS NOT NULL
                         AND sport = x.sport LIMIT 1))
                    WHERE x.processed = 0 AND x.sport IN ({marks}) AND g.status = 'ended'
                    ORDER BY x.game_start DESC LIMIT ?""", (*cfg.sports, batch * 5)).fetchall()
            if not rows:
                break
            by_game: dict[str, list] = {}
            for r in rows:
                by_game.setdefault(r["game_key"], []).append(r)
            games = list(by_game.items())[:batch]
            jobs = []
            with dbmod.tx(conn):
                for gk, rs in games:
                    sport = rs[0]["sport"]
                    if sport == "soccer" and (rs[0]["league"] or "").lower() not in cfg.soccer_leagues:
                        conn.executemany("UPDATE collector_hist_extras SET processed=2, payload='', updated_at=? "
                                         "WHERE condition_id=?", [(C.now(), r["condition_id"]) for r in rs])
                        continue
                    from polylab.collector.discover import _teams_from_db
                    teams = _teams_from_db(conn, gk, sport)
                    want = []
                    for r in rs:
                        m = json.loads(r["payload"])
                        mt = C.market_type_of(m, sport)
                        C.upsert_market(conn, C.market_row(m, game_key=gk, market_type=mt), ts)
                        trows = C.token_rows(m, mt, teams)
                        C.upsert_tokens(conn, m["conditionId"], trows)
                        want.extend(t["token_id"] for t in trows if t["outcome_index"] == 0)
                    start = rs[0]["start_time"]
                    end = rs[0]["ended_at"] or start + C.NOMINAL_GAME_SECONDS.get(sport, 3 * 3600)
                    jobs.append((gk, want, start - PRE_S, min(end + POST_S, ts), client))
            for gk, hist, err in pool.map(_fetch_prices, jobs):
                cids = [r["condition_id"] for r in by_game[gk]]
                with dbmod.tx(conn):
                    if err is not None:
                        log.warning("extras prices %s: %r", gk, err)
                        conn.executemany("UPDATE collector_hist_extras SET processed=3, updated_at=? WHERE condition_id=?",
                                         [(C.now(), c) for c in cids])
                        stats["extras_failed"] += len(cids)
                        continue
                    per_cid: dict[str, int] = {}
                    for tok, pts in hist.items():
                        prow = history_rows(pts, tok)
                        stats["extras_bars"] += C.write_price_bars(conn, prow)
                        cid = conn.execute("SELECT condition_id FROM tokens WHERE token_id=?", (tok,)).fetchone()
                        if cid:
                            per_cid[cid[0]] = per_cid.get(cid[0], 0) + len(prow)
                    for cid in cids:
                        n = per_cid.get(cid, 0)
                        _status(conn, cid, "prices", "done" if n else "empty", n, "historical extra (outcome 0)")
                    conn.executemany("UPDATE collector_hist_extras SET processed=1, payload='', updated_at=? "
                                     "WHERE condition_id=?", [(C.now(), c) for c in cids])
                    stats["extras_stored"] += len(cids)
    stats["extras_waiting"] = conn.execute("SELECT COUNT(*) FROM collector_hist_extras WHERE processed=0").fetchone()[0]
    return stats


def run_historical(conn, cfg: C.CollectorConfig, client: Client, ts: int, budget_s: float, since: int,
                   workers: int = 4) -> dict:
    ensure_hist_schema(conn)
    with dbmod.tx(conn):   # games whose price fetch failed last run get one more try per run
        conn.execute("UPDATE collector_hist_markets SET processed=0 WHERE processed=3")
        conn.execute("UPDATE collector_hist_extras SET processed=0 WHERE processed=3")
    t0 = time.time()
    deadline = t0 + budget_s
    size0 = db_bytes(conn)
    enum = {}
    # enumeration may use at most half the budget so every run also stores games (both phases resume)
    enum_deadline = t0 + budget_s / 2
    for sport in cfg.sports:
        if time.time() > enum_deadline:
            break
        st = enumerate_sport(conn, sport, since, client, enum_deadline)
        enum[sport] = {k: st.get(k) for k in ("pages", "staged", "done")}
    extras_start = deadline - budget_s * EXTRAS_BUDGET_SHARE
    stats = process_events(conn, cfg, client, ts, extras_start, workers=workers)
    # phase C: extra markets get the rest (at least EXTRAS_BUDGET_SHARE); enumeration may use half of that
    extras_enum = {}
    enum_deadline = time.time() + (deadline - time.time()) / 2
    for sport in cfg.sports:
        st = enumerate_extras(conn, sport, since, cfg, client, enum_deadline)
        extras_enum[sport] = {k: st.get(k) for k in ("pages", "staged", "done")}
    stats.update(process_extras(conn, cfg, client, ts, deadline, workers=workers))
    stats["extras_enumeration"] = extras_enum
    size1 = db_bytes(conn)
    remaining = {r["sport"]: r["n"] for r in conn.execute(
        "SELECT sport, COUNT(DISTINCT event_id) AS n FROM collector_hist_markets WHERE processed=0 GROUP BY sport")}
    stored_total = conn.execute("SELECT COUNT(DISTINCT event_id) FROM collector_hist_markets WHERE processed=1").fetchone()[0]
    per_event = (size1 - size0) / stats["events_stored"] if stats["events_stored"] else None
    rem_total = sum(remaining.values())
    return {"enumeration": enum, **stats, "events_remaining": remaining, "events_stored_total": stored_total,
            "core_db_growth_bytes": size1 - size0, "bytes_per_event": round(per_event) if per_event else None,
            "est_remaining_bytes_upper": round(per_event * rem_total) if per_event else None,
            "secs": round(time.time() - t0, 1)}


def db_bytes(conn) -> int:
    pc = conn.execute("PRAGMA page_count").fetchone()[0]
    ps = conn.execute("PRAGMA page_size").fetchone()[0]
    fl = conn.execute("PRAGMA freelist_count").fetchone()[0]
    return (pc - fl) * ps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab backfill", description="post-game and historical backfill")
    ap.add_argument("--recent", action="store_true", help="games ended in the last 48h")
    ap.add_argument("--historical", action="store_true", help="closed games since --since (moneyline 1-min prices)")
    ap.add_argument("--budget-minutes", type=float, default=10.0)
    ap.add_argument("--since", default="2026-02-01")
    ap.add_argument("--game", help="backfill one game_key (recent-style)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-trades", action="store_true")
    args = ap.parse_args(argv)
    if not (args.recent or args.historical or args.game):
        args.recent = True
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from polylab.settings import paths
    p = paths()
    conn = dbmod.core(p)
    client = default_client()
    cfg = C.load_config()
    ts = C.now()
    since = int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    run_id = C.job_start(conn, "backfill")
    summary: dict = {}
    ok = True
    budget = args.budget_minutes * 60
    t0 = time.time()
    try:
        if args.recent or args.game:
            summary["recent"] = run_recent(conn, client, ts, game=args.game, budget_s=budget,
                                           trades=not args.no_trades)
        if args.historical:
            left = max(30.0, budget - (time.time() - t0))
            summary["historical"] = run_historical(conn, cfg, client, ts, left, since, workers=args.workers)
    except Exception as exc:
        ok = False
        summary["error"] = repr(exc)[:500]
        log.exception("backfill failed")
    summary["http_calls"] = client.calls
    summary["http_mb"] = round(client.bytes_in / 1e6, 1)
    summary["core_db_bytes"] = db_bytes(conn)
    C.job_finish(conn, run_id, ok, summary)
    print(json.dumps(summary, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
