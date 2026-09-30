"""`polylab poll` / `run_once(paths)` — one-minute order-book + price snapshot for live / imminent games.

Contract for `polylab tick` (called every minute BEFORE strategies run; typical < 5 s, hard budget 25 s):
    run_once(paths, *, client=None, ts=None, cfg=None) -> dict
      {"ok": bool, "tokens": n_tracked, "books": n_books, "bars": n_poll_mid_bars, "games_live": n,
       "gamma_states": n, "quality": n_quality_rows, "secs": float, "books_db": "YYYY-MM",
       "pregame_tokens"/"pregame_books"/"pregame_bars": only on runs that did the pre-game pass}
    Never raises for API trouble (returns ok=False + "error"); only storage errors propagate.
    After it returns, core.db price_bars(source='poll_mid', ts=this minute) and books/YYYY-MM.db
    book_snapshots(source='poll') hold the freshest snapshot for every tracked token.

Sources:
- CLOB `POST /books` (<=200 tokens per call) for every token of open markets whose game is live or starts
  within 15 min (common.active_tokens) — every minute.
- Pre-game (for strategies that trade up to 120 h before start): the same batch call for the moneyline/draw
  tokens of games starting in 15 min..120 h, throttled to once per 10 min by checkpoint `poll.pregame.last`
  (common.pregame_tokens). Same tables (book_snapshots source='poll', price_bars source='poll_mid').
- Gamma `GET /events/keyset?id=..` for started games -> games.status/score and, when the stream daemon's
  sports feed is not fresh for that game, game_states(source='gamma') on change.
- Data API `GET /v2/live-volume?event_id=` every 5th minute for live games -> market_metrics.live_volume.
poll_mid is written only when the book is two-sided, uncrossed and spread <= 10c (Polymarket's own display rule);
the book snapshot itself is always stored.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from polylab import db as dbmod
from polylab.api import clob_public, data_api, gamma
from polylab.api.http import Client, default_client
from polylab.collector import common as C

log = logging.getLogger("polylab.poll")

# `/books` timestamp is the book's last change, so quiet books look old; only flag long silence during play.
STALE_BOOK_S = 900
GAP_S = 180
WS_FRESH_S = 300


def heartbeat_age(paths) -> float | None:
    hb = paths.state / "stream.heartbeat"
    try:
        return time.time() - hb.stat().st_mtime
    except OSError:
        return None


def run_once(paths, *, client: Client | None = None, ts: int | None = None,
             cfg: C.CollectorConfig | None = None) -> dict:
    t0 = time.time()
    client = client or default_client()
    cfg = cfg or C.load_config()
    ts = ts or C.now()
    conn = dbmod.core(paths)
    q = C.QualityLog()
    out: dict = {"ok": True, "tokens": 0, "books": 0, "bars": 0, "games_live": 0, "gamma_states": 0, "quality": 0}
    try:
        toks = C.active_tokens(conn, ts, cfg.poll_lead_s)
        out["tokens"] = len(toks)
        last = dbmod.get_checkpoint(conn, "poll.last_run")
        started_games = sorted({r["game_key"] for r in toks if (r["start_time"] or 0) <= ts or r["status"] == "live"})
        if last and started_games and ts - int(last) > GAP_S:
            q.add("poll_gap", None, ts - int(last), since=int(last), until=ts)
        if toks:
            try:
                books = clob_public.books([r["token_id"] for r in toks], client)
            except Exception as exc:
                out.update(ok=False, error=f"books: {exc!r}"[:300])
                books = {}
            # Receipt time is wall clock in production; an explicit historical `ts` (replays/tests) is kept as-is.
            recv = C.now() if abs(C.now() - ts) < 300 else ts
            out["books"], out["bars"] = _store_books(paths, conn, toks, books, recv, q, skip_missing=not out["ok"])
            out["books_db"] = dbmod.year_month(recv)
        _pregame(paths, conn, cfg, client, ts, q, out)
        if started_games:
            try:
                out["gamma_states"], out["games_live"] = _gamma_states(paths, conn, started_games, client, ts)
            except Exception as exc:
                out.update(ok=False, error=(out.get("error", "") + f" gamma: {exc!r}")[:300])
            if (ts // 60) % 5 == 0:
                try:
                    _live_volume(conn, started_games, client, ts)
                except Exception as exc:
                    log.warning("live volume failed: %r", exc)
        with dbmod.tx(conn):
            out["quality"] = q.flush(conn)
            dbmod.set_checkpoint(conn, "poll.last_run", str(ts))
    finally:
        conn.close()
    out["secs"] = round(time.time() - t0, 2)
    return out


def _pregame(paths, conn, cfg: C.CollectorConfig, client: Client, ts: int, q: C.QualityLog, out: dict) -> None:
    """Pre-game coverage (games starting in 15 min..120 h): one batch `POST /books` for all their moneyline/draw
    tokens at most every `cfg.pregame_every_s` (checkpoint poll.pregame.last) -> book_snapshots + poll_mid."""
    last = dbmod.get_checkpoint(conn, "poll.pregame.last")
    if last and ts - int(last) < cfg.pregame_every_s:
        return
    toks = C.pregame_tokens(conn, ts, cfg.poll_lead_s, cfg.pregame_horizon_s)
    out["pregame_tokens"] = len(toks)
    if toks:
        try:
            books = clob_public.books([r["token_id"] for r in toks], client)
        except Exception as exc:
            out.update(ok=False, error=(out.get("error", "") + f" pregame books: {exc!r}")[:300])
            return
        out["pregame_books"], out["pregame_bars"] = _store_books(paths, conn, toks, books, C.now(), q,
                                                                 skip_missing=False, stale_s=None)
    with dbmod.tx(conn):
        dbmod.set_checkpoint(conn, "poll.pregame.last", str(ts))


def _store_books(paths, conn, toks, books: dict, recv: int, q: C.QualityLog, skip_missing: bool,
                 stale_s: int | None = STALE_BOOK_S) -> tuple[int, int]:
    snaps = []
    bars = []
    for r in toks:
        tok = r["token_id"]
        b = books.get(tok)
        if b is None:
            if not skip_missing:
                q.add("missing_book", tok, game_key=r["game_key"])
            continue
        m = C.book_metrics(b)
        if m["crossed"]:
            q.add("crossed_book", tok, (m["best_bid"] or 0) - (m["best_ask"] or 0))
        if stale_s and m["exchange_ts"] and recv - m["exchange_ts"] > stale_s:
            q.add("stale_book", tok, recv - m["exchange_ts"])
        snaps.append((tok, recv, "poll", m["best_bid"], m["best_ask"], m["mid"], m["spread"], m["bid_depth_usd"],
                      m["ask_depth_usd"], m["imb_l1"], m["imb_l5"], m["imb_l10"], m["levels_z"], m["exchange_ts"]))
        if C.mid_is_price(m):
            bars.append((tok, C.minute(recv), "poll_mid", m["mid"], recv))
    if snaps:
        bconn = dbmod.books(paths, dbmod.year_month(recv))
        try:
            with dbmod.tx(bconn):
                bconn.executemany(
                    "INSERT OR REPLACE INTO book_snapshots(token_id, ts, source, best_bid, best_ask, mid, spread, "
                    "bid_depth_usd, ask_depth_usd, imb_l1, imb_l5, imb_l10, levels_z, exchange_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", snaps)
        finally:
            bconn.close()
    if bars:
        with dbmod.tx(conn):
            C.write_price_bars(conn, bars)
    return len(snaps), len(bars)


def _last_state(conn, game_key: str, source: str):
    return conn.execute(
        "SELECT ts, status, period, elapsed, home_score, away_score, live, ended FROM game_states "
        "WHERE game_key=? AND source=? ORDER BY ts DESC LIMIT 1", (game_key, source)).fetchone()


def _gamma_states(paths, conn, game_keys: list[str], client: Client, ts: int) -> tuple[int, int]:
    events = gamma.events_by_ids(game_keys, client)
    hb_age = heartbeat_age(paths)
    written = live = 0
    sports = {r["game_key"]: r["sport"] for r in conn.execute(
        f"SELECT game_key, sport FROM games WHERE game_key IN ({','.join('?' * len(game_keys))})", game_keys)}
    with dbmod.tx(conn):
        for ev in events:
            gk = str(ev["id"])
            sport = sports.get(gk)
            if not sport:
                continue
            row = C.game_row(ev, sport, source="discover")
            C.upsert_game(conn, row, ts)
            live += 1 if row["status"] == "live" else 0
            ws = _last_state(conn, gk, "ws_sports")
            ws_fresh = hb_age is not None and hb_age < 180 and ws is not None and ts - ws["ts"] < WS_FRESH_S
            if ws_fresh:
                continue
            state = (row["status"], _s(ev.get("period")), _s(ev.get("elapsed")), row["home_score"], row["away_score"])
            prev = _last_state(conn, gk, "gamma")
            if prev is not None and (prev["status"], prev["period"], prev["elapsed"], prev["home_score"],
                                     prev["away_score"]) == state:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO game_states(game_key, ts, received_at, source, status, live, ended, period, "
                "elapsed, game_minute, home_score, away_score, raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (gk, ts, C.now(), "gamma", row["status"], _b(ev.get("live")), _b(ev.get("ended")), state[1], state[2],
                 C.game_minute(sport, ev.get("period"), ev.get("elapsed"), row["status"]),
                 row["home_score"], row["away_score"],
                 json.dumps({k: ev.get(k) for k in ("score", "period", "elapsed", "live", "ended", "finishedTimestamp")},
                            separators=(",", ":"))))
            written += 1
    return written, live


def _live_volume(conn, game_keys: list[str], client: Client, ts: int) -> None:
    rows = []
    for gk in game_keys:
        vols = data_api.live_volume(gk, client)
        rows.extend((cid, C.minute(ts), v) for cid, v in vols.items() if not cid.startswith("_"))
    if not rows:
        return
    tracked = {r[0] for r in conn.execute(
        f"SELECT condition_id FROM markets WHERE game_key IN ({','.join('?' * len(game_keys))})", game_keys)}
    with dbmod.tx(conn):
        for cid, t, v in rows:
            if cid in tracked:
                conn.execute(
                    "INSERT INTO market_metrics(condition_id, ts, live_volume) VALUES(?,?,?) "
                    "ON CONFLICT(condition_id, ts) DO UPDATE SET live_volume=excluded.live_volume", (cid, t, v))


def _s(v) -> str | None:
    return None if v is None or v == "" else str(v)


def _b(v) -> int | None:
    return None if v is None else (1 if v else 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab poll", description="one order-book/price snapshot of live games")
    ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from polylab.settings import paths
    p = paths()
    conn = dbmod.core(p)
    run_id = C.job_start(conn, "poll")
    out = run_once(p)
    out["http_calls"] = default_client().calls
    C.job_finish(conn, run_id, out["ok"], out)
    conn.close()
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
