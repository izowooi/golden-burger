"""`polylab general backfill [--budget-minutes N] [--since 2026-02-01] [--min-volume 10000] [--fidelity 5]`

Resumable, budgeted history of closed near-resolution markets (every category) for the cherry backtest:
  phase A (<= --enum-share of the budget): Gamma `GET /markets/keyset?closed=true&include_tag=true
    &volume_num_min=<floor>&end_date_min=<day>&end_date_max=<day+1d>&start_date_max=<day-2d>` one UTC day at a time,
    from --since to yesterday (checkpoint `general.hist.enum` = {params, day, cursor}). `start_date_max` drops markets
    listed less than ~2-3 days before their end (15-minute crypto, daily weather) server-side; cherry's
    `min_listed_hours` (72) applies the exact rule on end_ref.
  phase B: markets with hist_status NULL, one end_ref day at a time in a hashed (time-uniform) day order so a partial
    backfill is an unbiased sample over the period: CLOB `POST /batch-prices-history` (<= 20 YES tokens per call, one
    shared window <= 15 days) at --fidelity over [end_ref - 4.5 d, min(close, end_ref + 4 d) + 1 h].
Rows: gen_quotes src=1 with p only (no bid/ask/size: history cannot give them). Change-only with an hourly heartbeat
(store.HIST_HEARTBEAT_S); a hole longer than that is missing data. INSERT OR IGNORE: poll rows of the same minute win.
Sports markets that core.db tracks are fetched as well (in_core=1): core.db history starts 1 h before kickoff, so
the 3-day pre-end window would otherwise be missing for them.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import logging
import time
from datetime import datetime, timezone

from polylab import db as dbmod
from polylab.api import clob_public, gamma
from polylab.api.http import Client, default_client
from polylab.collector import common as C
from polylab.general import discover as D
from polylab.general import store

log = logging.getLogger("polylab.general.backfill")

ENUM_CHECKPOINT = "general.hist.enum"
DEFAULT_MIN_VOLUME = 10000.0
DAY = 86400
BATCH = 20
MAX_UNION_S = 14 * DAY


def enum_params(min_volume: float) -> dict:
    return {"closed": "true", "include_tag": "true", "volume_num_min": min_volume, "limit": 100}


def enumerate_closed(reg, paths, since: int, until: int, client: Client, deadline: float,
                     min_volume: float = DEFAULT_MIN_VOLUME) -> dict:
    base = enum_params(min_volume)
    state = json.loads(dbmod.get_checkpoint(reg, ENUM_CHECKPOINT) or "{}")
    if state.get("params") != base or state.get("since") != since:
        state = {"params": base, "since": since, "day": since, "cursor": None, "pages": 0, "staged": 0}
    ts = C.now()
    while state["day"] + DAY <= until and time.time() < deadline:
        day = state["day"]
        params = {**base, "end_date_min": gamma.iso(day), "end_date_max": gamma.iso(day + DAY),
                  "start_date_max": gamma.iso(day - 2 * DAY)}
        finished_day = True
        for rows, cursor in gamma.markets_keyset_pages(params, client, start_cursor=state.get("cursor")):
            staged = [r for r in (D.market_row(m, ts=ts, source="history_enum") for m in rows) if r is not None]
            with dbmod.tx(reg):
                D.upsert_rows(reg, paths, staged)
                state["staged"] += len(staged)
                state["pages"] += 1
                state["cursor"] = cursor
                if not cursor or not rows:
                    state["day"], state["cursor"] = day + DAY, None
                dbmod.set_checkpoint(reg, ENUM_CHECKPOINT, json.dumps(state))
            if time.time() > deadline and cursor and rows:
                finished_day = False
                break
        if not finished_day:
            break
    state["done"] = state["day"] + DAY > until
    return state


def history_window(m, now: int) -> tuple[int, int] | None:
    e = m["end_ref"]
    if e is None:
        return None
    lo = e - store.HIST_BEFORE_END_S
    listed = m["start_date"] or m["created_at"]
    if listed:
        lo = max(lo, int(listed))
    close = m["closed_at"] or (e + store.HIST_AFTER_END_S)
    hi = min(close, e + store.HIST_AFTER_END_S) + 3600
    hi = min(hi, now)
    if hi <= lo:
        return None
    return lo, hi


def history_rows(mid: int, points: list[tuple[int, float]], lo: int, hi: int,
                 heartbeat_s: int = store.HIST_HEARTBEAT_S) -> list[tuple]:
    """gen_quotes rows (QUOTE_COLS order): last point per minute, change-only with a heartbeat, inside [lo, hi]."""
    b: dict[int, float] = {}
    for t, p in sorted(points):
        if lo <= t <= hi:
            b[C.minute(t)] = p
    out, last_ts, last_p = [], None, None
    for t in sorted(b):
        p = b[t]
        if last_ts is not None and p == last_p and t - last_ts < heartbeat_s:
            continue
        out.append((mid, t, None, None, None, None, None, p, store.SRC_HISTORY))
        last_ts, last_p = t, p
    return out


def _batches(rows, now: int) -> list[list]:
    """Greedy batches of <= 20 markets sorted by end_ref whose union window stays <= 14 days."""
    out, cur, lo, hi = [], [], None, None
    for m in sorted(rows, key=lambda r: r["end_ref"] or 0):
        w = history_window(m, now)
        if w is None:
            out.append([(m, None)])
            continue
        nlo, nhi = (w[0], w[1]) if lo is None else (min(lo, w[0]), max(hi, w[1]))
        if cur and (len(cur) >= BATCH or nhi - nlo > MAX_UNION_S):
            out.append(cur)
            cur, nlo, nhi = [], w[0], w[1]
        cur.append((m, w))
        lo, hi = nlo, nhi
    if cur:
        out.append(cur)
    return out


def _fetch(job):
    batch, fidelity, client = job
    live = [(m, w) for m, w in batch if w is not None]
    if not live:
        return batch, {}, None
    lo, hi = min(w[0] for _, w in live), max(w[1] for _, w in live)
    try:
        hist = clob_public.batch_prices_history([m["yes_token"] for m, _ in live], lo, hi, fidelity=fidelity,
                                                client=client)
        return batch, hist, None
    except Exception as exc:  # noqa: BLE001
        return batch, {}, exc


def next_day(reg, now: int) -> int | None:
    """A todo end_ref day in hashed order (time-uniform sampling of a partial backfill)."""
    r = reg.execute("SELECT end_ref / 86400 AS d FROM gen_markets WHERE hist_status IS NULL AND closed=1 "
                    "AND end_ref IS NOT NULL AND end_ref <= ? GROUP BY d ORDER BY (d * 2654435761) % 4294967296 "
                    "LIMIT 1", (now - 3600,)).fetchone()
    return None if r is None else int(r["d"])


def process(paths, reg, client: Client, now: int, deadline: float, workers: int = 4, fidelity: int = 5,
            min_volume: float = DEFAULT_MIN_VOLUME) -> dict:
    stats = {"days": 0, "markets_done": 0, "markets_empty": 0, "markets_failed": 0, "markets_skipped": 0,
             "rows": 0, "points": 0}
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        while time.time() < deadline:
            d = next_day(reg, now)
            if d is None:
                break
            todo = reg.execute("SELECT * FROM gen_markets WHERE hist_status IS NULL AND closed=1 AND end_ref <= ? "
                               "AND end_ref / 86400 = ?", (now - 3600, d)).fetchall()
            low = [m["id"] for m in todo if (m["volume"] or 0) < min_volume]
            if low:
                with dbmod.tx(reg):
                    reg.executemany(f"UPDATE gen_markets SET hist_status='skip', hist_updated_at={int(now)} WHERE id=?",
                                    [(i,) for i in low])
                stats["markets_skipped"] += len(low)
            todo = [m for m in todo if (m["volume"] or 0) >= min_volume]
            if not todo:
                continue
            stats["days"] += 1
            jobs = [(b, fidelity, client) for b in _batches(todo, now)]
            for batch, hist, err in pool.map(_fetch, jobs):
                writes, status = [], []
                for m, w in batch:
                    if w is None:
                        status.append(("empty", 0, None, None, m["id"]))
                        stats["markets_empty"] += 1
                        continue
                    if err is not None:
                        status.append(("failed", None, None, None, m["id"]))
                        stats["markets_failed"] += 1
                        continue
                    pts = hist.get(m["yes_token"], [])
                    stats["points"] += len(pts)
                    rows = history_rows(m["id"], pts, w[0], w[1])
                    writes.extend(rows)
                    status.append(("done" if rows else "empty", len(rows), w[0], w[1], m["id"]))
                    stats["markets_done" if rows else "markets_empty"] += 1
                if err is not None:
                    log.warning("history batch failed: %r", err)
                stats["rows"] += store.write_quotes(paths, writes)
                with dbmod.tx(reg):
                    reg.executemany("UPDATE gen_markets SET hist_status=?, hist_rows=?, hist_from=?, hist_until=?, "
                                    f"hist_updated_at={int(now)} WHERE id=?", status)
                    if err is not None:
                        store.quality(reg, "general_history_failed", None, error=repr(err)[:300],
                                      ids=[m["id"] for m, _ in batch][:5])
    return stats


def run(paths, client: Client, now: int, budget_s: float, since: int, *, workers: int = 4, fidelity: int = 5,
        min_volume: float = DEFAULT_MIN_VOLUME, enum_share: float = 0.5) -> dict:
    reg = store.registry(paths)
    t0 = time.time()
    try:
        with dbmod.tx(reg):     # one more try per run for failed fetches
            reg.execute("UPDATE gen_markets SET hist_status=NULL WHERE hist_status='failed'")
        until = now - now % DAY
        enum = enumerate_closed(reg, paths, since, until, client, t0 + budget_s * enum_share, min_volume)
        stats = process(paths, reg, client, now, t0 + budget_s, workers=workers, fidelity=fidelity,
                        min_volume=min_volume)
        counts = {r[0] or "todo": r[1] for r in reg.execute(
            "SELECT hist_status, COUNT(*) FROM gen_markets WHERE source='history_enum' GROUP BY 1")}
    finally:
        reg.close()
    return {"enumeration": {k: enum.get(k) for k in ("day", "pages", "staged", "done")}, **stats,
            "hist_status": counts, "secs": round(time.time() - t0, 1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab general backfill", description="general-market history (resumable)")
    ap.add_argument("--budget-minutes", type=float, default=20.0)
    ap.add_argument("--since", default="2026-02-01")
    ap.add_argument("--min-volume", type=float, default=DEFAULT_MIN_VOLUME)
    ap.add_argument("--fidelity", type=int, default=5, choices=(1, 5, 10, 15, 60))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--enum-share", type=float, default=0.5, help="max share of the budget for phase A")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from polylab.settings import paths
    p = paths()
    since = int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    reg = store.registry(p)
    run_id = store.job_start(reg, "general-backfill")
    client = default_client()
    try:
        out = run(p, client, C.now(), args.budget_minutes * 60, since, workers=args.workers, fidelity=args.fidelity,
                  min_volume=args.min_volume, enum_share=args.enum_share)
    except Exception as exc:
        store.job_finish(reg, run_id, False, {"error": repr(exc)[:500]})
        raise
    out["http_calls"] = client.calls
    out["http_mb"] = round(client.bytes_in / 1e6, 1)
    store.job_finish(reg, run_id, True, out)
    reg.close()
    print(json.dumps(out, indent=1, default=str))
    return 0
