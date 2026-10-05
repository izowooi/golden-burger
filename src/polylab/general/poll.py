"""`polylab general poll [--every-minutes N]` — YES-token level-1 quotes of registered open general markets.

Source: CLOB `POST /books` (500 tokens per call, a few calls in parallel, bucket `clob_books`). Only the YES
(outcome index 0) book is fetched: binary books mirror, so NO bid = 1 - YES ask and NO ask = 1 - YES bid.

Selection: registry `closed = 0`, `in_core = 0` (core.db's poll already covers those) and end_ref within
[now - 4 d, now + 4 d] (positions can be held past end_ref until resolution).
Row rule (gen_quotes, monthly shard): a market gets a row when bid/ask/L1 size/last differs from its previously
stored row, or when that row is >= 10 minutes old (heartbeat), like ou05. A longer hole is missing data.
Budget knob: `--every-minutes 5` (or env POLYLAB_GENERAL_POLL_EVERY=5) keeps the every-minute cron but only polls on
minutes divisible by N — the 5-minute fallback of the storage budget. Caveat: the engine's paper broker
requires books <= 180 s old, so with N = 5 most non-core cherry paper entries/exits go unfilled; use only when the
budget really demands it.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import os
import time

from polylab import db as dbmod
from polylab.api import clob_public
from polylab.api.http import Client, default_client
from polylab.collector import common as C
from polylab.general import store

log = logging.getLogger("polylab.general.poll")

BOOKS_CHUNK = 500
WORKERS = 4
HOLD_AFTER_END_S = 4 * 86400
FIELDS = ("bid", "ask", "bid_sz", "ask_sz", "last")


def fetch_books(tokens: list[str], client: Client) -> dict[str, dict]:
    chunks = [tokens[i:i + BOOKS_CHUNK] for i in range(0, len(tokens), BOOKS_CHUNK)]
    out: dict[str, dict] = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, min(WORKERS, len(chunks)))) as pool:
        for part in pool.map(lambda c: clob_public.books(c, client, chunk=BOOKS_CHUNK), chunks):
            out.update(part)
    return out


def level1(book: dict) -> dict:
    bids = C._levels(book.get("bids"), reverse=True)
    asks = C._levels(book.get("asks"), reverse=False)
    last = C._float(book.get("last_trade_price")) if book.get("last_trade_price") not in (None, "") else None
    return {"bid": bids[0][0] if bids else None, "ask": asks[0][0] if asks else None,
            "bid_sz": bids[0][1] if bids else None, "ask_sz": asks[0][1] if asks else None, "last": last}


def quote_row(mid: int, ts: int, l1: dict) -> tuple:
    p = round((l1["bid"] + l1["ask"]) / 2, 6) if l1["bid"] is not None and l1["ask"] is not None else None
    return (mid, ts, l1["bid"], l1["ask"], l1["bid_sz"], l1["ask_sz"], l1["last"], p, store.SRC_POLL)


def signature(l1: dict) -> str:
    return json.dumps([l1[k] for k in FIELDS], separators=(",", ":"))


def every_minutes(arg: int | None) -> int:
    if arg:
        return max(1, int(arg))
    try:
        return max(1, int(os.environ.get("POLYLAB_GENERAL_POLL_EVERY", "1")))
    except ValueError:
        return 1


def run_once(paths, *, client: Client | None = None, ts: int | None = None, every: int = 1) -> dict:
    t0 = time.time()
    ts = ts or C.now()
    minute = C.minute(ts)
    if every > 1 and (minute // 60) % every:
        return {"ok": True, "skipped": f"every {every} min"}
    client = client or default_client()
    reg = store.registry(paths)
    out: dict = {"ok": True, "every": every, "markets": 0, "books": 0, "rows": 0, "unchanged": 0, "heartbeat": 0,
                 "missing": 0, "one_sided": 0}
    try:
        markets = reg.execute(
            "SELECT id, yes_token FROM gen_markets WHERE closed=0 AND in_core=0 AND end_ref BETWEEN ? AND ?",
            (ts - HOLD_AFTER_END_S, ts + store.WINDOW_BEFORE_END_S)).fetchall()
        out["markets"] = len(markets)
        last_run = dbmod.get_checkpoint(reg, "general.poll.last_run")
        gap = ts - int(last_run) if last_run else None
        try:
            books = fetch_books([m["yes_token"] for m in markets], client) if markets else {}
        except Exception as exc:  # noqa: BLE001 — a failed minute is a recorded gap, not a crash
            out.update(ok=False, error=f"books: {exc!r}"[:300])
            books = {}
        out["books"] = len(books)
        last = {r[0]: (r[1], r[2]) for r in reg.execute("SELECT id, ts, sig FROM gen_last")}
        rows, updates = [], []
        for m in markets:
            b = books.get(m["yes_token"])
            if b is None:
                out["missing"] += 1
                continue
            l1 = level1(b)
            if l1["bid"] is None or l1["ask"] is None:
                out["one_sided"] += 1
            sig = signature(l1)
            prev = last.get(m["id"])
            if prev is not None and sig == prev[1] and minute - prev[0] < store.POLL_HEARTBEAT_S:
                out["unchanged"] += 1
                continue
            if prev is not None and sig == prev[1]:
                out["heartbeat"] += 1
            rows.append(quote_row(m["id"], minute, l1))
            updates.append((m["id"], minute, sig))
        out["rows"] = store.write_quotes(paths, rows)
        with dbmod.tx(reg):
            reg.executemany("INSERT OR REPLACE INTO gen_last(id, ts, sig) VALUES(?,?,?)", updates)
            if gap is not None and gap > store.GAP_S * every and markets:
                store.quality(reg, "general_poll_gap", None, seconds=gap, since=int(last_run), until=ts)
            if out["missing"]:
                store.quality(reg, "general_missing_book", None, count=out["missing"])
            dbmod.set_checkpoint(reg, "general.poll.last_run", str(ts))
    finally:
        reg.close()
    out["secs"] = round(time.time() - t0, 2)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="polylab general poll", description=__doc__.split("\n")[0])
    ap.add_argument("--every-minutes", type=int, default=None)
    args = ap.parse_args(argv)
    from polylab.settings import paths
    p = paths()
    every = every_minutes(args.every_minutes)
    if every > 1 and (C.minute(C.now()) // 60) % every:
        print(json.dumps({"ok": True, "skipped": f"every {every} min"}))
        return 0
    reg = store.registry(p)
    run_id = store.job_start(reg, "general-poll")
    reg.close()
    out = run_once(p, every=every)
    out["http_calls"] = default_client().calls
    reg = store.registry(p)
    store.job_finish(reg, run_id, out["ok"], out)
    reg.close()
    print(json.dumps(out))
    return 0 if out["ok"] else 1
