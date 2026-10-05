"""`polylab ou05 poll` (every minute) — level-1 quotes of BOTH sides of every registered open O/U 0.5 market.

Source: CLOB `POST /books` (500 tokens per call, a few calls in parallel; ~1.9k tokens took 1.4–2 s on
2026-10-05). Bucket `clob_books` keeps the client-side rate limit (half of the documented 500 req / 10 s).

Row rule (ou05_quotes, monthly shard by minute): a market gets a row when any quote field (bid/ask/L1 size/last
of either side) differs from its previously stored row, OR when the previous row is >= 10 minutes old
(heartbeat). So inside a covered period no market goes 10 minutes without a row; a longer hole is missing data,
not an unchanged quote. The previous row's signature is persisted in registry `ou05_last`.

Selection: registry `closed = 0` and kickoff later than 24 h ago (discover logs `ou05_stale_open` beyond that).
game_status: core.db `games.status` when the game is tracked there (read-only), else clock `pre|started`.
Quality (registry quality_events): `ou05_poll_gap` (runs > 3 min apart), `ou05_mirror_break` (two-sided books
where under_ask != 1 − over_bid or under_bid != 1 − over_ask — 0 expected: Polymarket mirrors complement books),
`ou05_missing_book` (aggregated count).
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import sqlite3
import time

from polylab import db as dbmod
from polylab.api import clob_public
from polylab.api.http import Client, default_client
from polylab.collector import common as C
from polylab.ou05 import discover as D
from polylab.ou05 import store

log = logging.getLogger("polylab.ou05.poll")

BOOKS_CHUNK = 500
WORKERS = 4
MIRROR_TOL = 1e-6
QUOTE_FIELDS = ("over_bid", "over_ask", "under_bid", "under_ask", "over_bid_sz", "over_ask_sz", "under_bid_sz",
                "under_ask_sz", "over_last", "under_last")


def fetch_books(tokens: list[str], client: Client, chunk: int = BOOKS_CHUNK, workers: int = WORKERS) -> dict[str, dict]:
    chunks = [tokens[i:i + chunk] for i in range(0, len(tokens), chunk)]
    out: dict[str, dict] = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, min(workers, len(chunks)))) as pool:
        for part in pool.map(lambda c: clob_public.books(c, client, chunk=chunk), chunks):
            out.update(part)
    return out


def level1(book: dict | None) -> dict:
    """best bid/ask + their sizes and last trade of one book; None fields when a side is empty."""
    if book is None:
        return {"bid": None, "ask": None, "bid_sz": None, "ask_sz": None, "last": None}
    bids = C._levels(book.get("bids"), reverse=True)
    asks = C._levels(book.get("asks"), reverse=False)
    last = C._float(book.get("last_trade_price")) if book.get("last_trade_price") not in (None, "") else None
    return {"bid": bids[0][0] if bids else None, "ask": asks[0][0] if asks else None,
            "bid_sz": bids[0][1] if bids else None, "ask_sz": asks[0][1] if asks else None, "last": last}


def _mid(l1: dict) -> float | None:
    return round((l1["bid"] + l1["ask"]) / 2, 6) if l1["bid"] is not None and l1["ask"] is not None else None


def _sum(a, b) -> float | None:
    return round(a + b, 6) if a is not None and b is not None else None


def quote_row(cid: str, ts: int, over: dict, under: dict, game_start: int | None, status: str | None,
              source: str = "poll") -> dict:
    return {"condition_id": cid, "ts": ts,
            "over_bid": over["bid"], "over_ask": over["ask"], "under_bid": under["bid"], "under_ask": under["ask"],
            "over_bid_sz": over["bid_sz"], "over_ask_sz": over["ask_sz"],
            "under_bid_sz": under["bid_sz"], "under_ask_sz": under["ask_sz"],
            "over_mid": _mid(over), "under_mid": _mid(under),
            "sum_ask": _sum(over["ask"], under["ask"]), "sum_bid": _sum(over["bid"], under["bid"]),
            "over_last": over["last"], "under_last": under["last"],
            "minutes_to_kickoff": store.minutes_to_kickoff(ts, game_start),
            "game_status": status or store.clock_status(ts, game_start), "source": source}


def signature(row: dict) -> str:
    return json.dumps([row[k] for k in QUOTE_FIELDS], separators=(",", ":"))


def mirror_broken(row: dict) -> bool:
    pairs = ((row["under_ask"], row["over_bid"]), (row["under_bid"], row["over_ask"]))
    return any(a is not None and b is not None and abs(a - (1 - b)) > MIRROR_TOL for a, b in pairs)


def should_store(row: dict, last: tuple[int, str] | None, heartbeat_s: int = store.HEARTBEAT_S) -> bool:
    if last is None:
        return True
    last_ts, last_sig = last
    return signature(row) != last_sig or row["ts"] - last_ts >= heartbeat_s


def _core_status(paths, game_keys: list[str]) -> dict[str, str]:
    """games.status from core.db (read-only) for games the main collector tracks; {} when unavailable."""
    if not game_keys or not paths.core_db.exists():
        return {}
    try:
        conn = dbmod.core(paths, readonly=True)
    except sqlite3.Error:
        return {}
    try:
        out = {}
        for i in range(0, len(game_keys), 500):
            part = game_keys[i:i + 500]
            out.update({r[0]: r[1] for r in conn.execute(
                f"SELECT game_key, status FROM games WHERE game_key IN ({','.join('?' * len(part))})", part)})
        return out
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def run_once(paths, *, client: Client | None = None, ts: int | None = None) -> dict:
    t0 = time.time()
    client = client or default_client()
    ts = ts or C.now()
    minute = C.minute(ts)
    reg = store.registry(paths)
    out: dict = {"ok": True, "markets": 0, "tokens": 0, "books": 0, "rows": 0, "unchanged": 0, "heartbeat": 0,
                 "missing": 0, "mirror_break": 0, "one_sided": 0}
    try:
        markets = reg.execute(
            "SELECT condition_id, game_key, over_token, under_token, game_start FROM ou05_markets "
            "WHERE closed=0 AND (game_start IS NULL OR game_start >= ?)", (ts - D.STALE_OPEN_S,)).fetchall()
        out["markets"] = len(markets)
        last_run = dbmod.get_checkpoint(reg, "ou05.poll.last_run")
        gap = ts - int(last_run) if last_run else None
        tokens = [t for m in markets for t in (m["over_token"], m["under_token"])]
        out["tokens"] = len(tokens)
        t1 = time.time()
        try:
            books = fetch_books(tokens, client) if tokens else {}
        except Exception as exc:  # noqa: BLE001 — a failed minute is a recorded gap, not a crash
            out.update(ok=False, error=f"books: {exc!r}"[:300])
            books = {}
        out["fetch_secs"] = round(time.time() - t1, 2)
        out["books"] = len(books)
        recv_min = C.minute(C.now()) if abs(C.now() - ts) < 300 else minute
        status = _core_status(paths, sorted({m["game_key"] for m in markets if m["game_key"]}))
        last = {r[0]: (r[1], r[2]) for r in reg.execute("SELECT condition_id, ts, sig FROM ou05_last")}
        rows, last_updates, broken = [], [], []
        for m in markets:
            ob, ub = books.get(m["over_token"]), books.get(m["under_token"])
            if ob is None and ub is None:
                out["missing"] += 1
                continue
            row = quote_row(m["condition_id"], recv_min, level1(ob), level1(ub), m["game_start"],
                            status.get(m["game_key"]))
            if row["sum_ask"] is None or row["sum_bid"] is None:
                out["one_sided"] += 1
            if mirror_broken(row):
                out["mirror_break"] += 1
                broken.append(m["condition_id"])
            prev = last.get(m["condition_id"])
            if not should_store(row, prev):
                out["unchanged"] += 1
                continue
            if prev is not None and signature(row) == prev[1]:
                out["heartbeat"] += 1
            rows.append(tuple(row[k] for k in store.QUOTE_COLS))
            last_updates.append((m["condition_id"], recv_min, signature(row)))
        out["rows"] = store.write_quotes(paths, rows)
        out["shard"] = dbmod.year_month(recv_min)
        with dbmod.tx(reg):
            reg.executemany("INSERT OR REPLACE INTO ou05_last(condition_id, ts, sig) VALUES(?,?,?)", last_updates)
            if gap is not None and gap > store.GAP_S and markets:
                store.quality(reg, "ou05_poll_gap", None, seconds=gap, since=int(last_run), until=ts)
            if broken:
                store.quality(reg, "ou05_mirror_break", None, count=len(broken), sample=broken[:5])
            if out["missing"]:
                store.quality(reg, "ou05_missing_book", None, count=out["missing"])
            dbmod.set_checkpoint(reg, "ou05.poll.last_run", str(ts))
    finally:
        reg.close()
    out["secs"] = round(time.time() - t0, 2)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="polylab ou05 poll", description=__doc__.split("\n")[0])
    ap.parse_args(argv)
    from polylab.settings import paths
    p = paths()
    reg = store.registry(p)
    run_id = store.job_start(reg, "ou05-poll")
    out = run_once(p)
    out["http_calls"] = default_client().calls
    store.job_finish(reg, run_id, out["ok"], out)
    reg.close()
    print(json.dumps(out))
    return 0 if out["ok"] else 1
