"""`polylab ou05 backfill [--budget-minutes N] [--since 2026-02-01] [--majors-only] [--workers 4]`

Resumable, budgeted mid-price history for O/U 0.5 markets (closed since --since, and open ones):
  phase A (<= half the budget): Gamma `GET /markets/keyset?tag_id=100350&closed=true&sports_market_types=totals
    &start_date_min=<since-30d>` walked by cursor (checkpoint `ou05.hist.enum` holds cursor + exact params; a
    param change restarts the walk). Line 0.5 rows are upserted into the registry (source='history_enum') with
    resolution from outcomePrices. Kickoff before --since is skipped.
  phase B: markets with `hist_status IS NULL`, major competitions (common.MAJOR_SOCCER_LEAGUES) first, then the
    newest kickoff: CLOB `POST /batch-prices-history` fidelity=1 for BOTH tokens over [createdAt, closedTime]
    (open markets: [createdAt, first poll row or now]) in <= 15-day windows.
Rows: ou05_quotes source='history' with over_mid / under_mid only. Each token is bucketed to minutes on its own
(last point in a minute wins); a side without a point in that minute is NULL — never carried forward. A row is
stored when either side shows a new value or the previous stored row is >= 10 min old (same rule as poll).
INSERT OR IGNORE: poll rows of the same minute win.

What history can NOT give (docs/research/ou05-overround-study.md): bid, ask, sizes, last trade, so no sum_ask /
sum_bid / spread. Polymarket's Over and Under books are mirrors, and the two `p` series sum to 1.000 (max
deviation 0.015 on 4 checked markets), so the historical Over+Under sum is NOT informative about overround.
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
from polylab.ou05 import discover as D
from polylab.ou05 import store

log = logging.getLogger("polylab.ou05.backfill")

POST_CLOSE_S = 1800
ENUM_CHECKPOINT = "ou05.hist.enum"


def enum_params(since: int) -> dict:
    """Fixed query (no now-derived values) so the keyset cursor stays valid across runs."""
    return {"tag_id": D.SOCCER_TAG, "closed": "true", "sports_market_types": "totals",
            "start_date_min": gamma.iso(since - 30 * 86400), "limit": 100}


def enumerate_closed(reg, since: int, client: Client, deadline: float) -> dict:
    params = enum_params(since)
    state = json.loads(dbmod.get_checkpoint(reg, ENUM_CHECKPOINT) or "{}")
    if state.get("params") != params:
        state = {"params": params, "cursor": None, "done": False, "pages": 0, "staged": 0}
    if state.get("done"):
        return state
    ts = C.now()
    for rows, cursor in gamma.markets_keyset_pages(params, client, start_cursor=state.get("cursor")):
        with dbmod.tx(reg):
            for m in rows:
                if not m.get("conditionId") or not D.is_ou05(m):
                    continue
                row = D.market_row(m, ts=ts, source="history_enum")
                if row["game_start"] is None or row["game_start"] < since:
                    continue
                D.upsert(reg, row)
                state["staged"] += 1
            state["pages"] += 1
            state["cursor"] = cursor
            state["done"] = not cursor or not rows
            dbmod.set_checkpoint(reg, ENUM_CHECKPOINT, json.dumps(state))
        if state["done"] or time.time() > deadline:
            break
    return state


def history_quote_rows(cid: str, over_pts: list[tuple[int, float]], under_pts: list[tuple[int, float]],
                       game_start: int | None, heartbeat_s: int = store.HEARTBEAT_S) -> list[tuple]:
    """Minute rows (QUOTE_COLS order) from the two tokens' prices-history points, change-or-heartbeat deduped."""
    def bucket(points):
        b: dict[int, float] = {}
        for t, p in sorted(points):
            b[C.minute(t)] = p
        return b
    ob, ub = bucket(over_pts), bucket(under_pts)
    out = []
    last_ts = last_o = last_u = None
    for t in sorted(set(ob) | set(ub)):
        o, u = ob.get(t), ub.get(t)
        changed = (o is not None and o != last_o) or (u is not None and u != last_u)
        if not changed and last_ts is not None and t - last_ts < heartbeat_s:
            continue
        row = {k: None for k in store.QUOTE_COLS}
        row.update(condition_id=cid, ts=t, over_mid=o, under_mid=u, source="history",
                   minutes_to_kickoff=store.minutes_to_kickoff(t, game_start),
                   game_status=store.clock_status(t, game_start))
        out.append(tuple(row[k] for k in store.QUOTE_COLS))
        last_ts = t
        last_o = o if o is not None else last_o
        last_u = u if u is not None else last_u
    return out


def first_poll_ts(paths, cid: str) -> int | None:
    for p in store.shard_paths(paths):
        conn = dbmod.connect(p, readonly=True)
        try:
            r = conn.execute("SELECT MIN(ts) FROM ou05_quotes WHERE condition_id=? AND source='poll'", (cid,)).fetchone()
        finally:
            conn.close()
        if r and r[0] is not None:
            return int(r[0])          # shards are scanned oldest first
    return None


def _window(paths, m, ts: int) -> tuple[int, int] | None:
    lo = m["created_at"] or (m["game_start"] - 30 * 86400 if m["game_start"] else None)
    if lo is None:
        return None
    if m["closed"]:
        hi = min((m["closed_at"] or (m["game_start"] or ts) + 4 * 3600) + POST_CLOSE_S, ts)
        return lo, hi
    return lo, min(first_poll_ts(paths, m["condition_id"]) or ts, ts)


def _fetch(job):
    cid, over, under, lo, hi, client = job
    try:
        return cid, clob_public.batch_prices_history([over, under], lo, hi, fidelity=1, client=client), None
    except Exception as exc:  # noqa: BLE001
        return cid, {}, exc


def process(paths, reg, client: Client, ts: int, deadline: float, workers: int = 4, batch: int = 16,
            majors_only: bool = False) -> dict:
    stats = {"markets_done": 0, "markets_empty": 0, "markets_failed": 0, "rows": 0, "points": 0}
    majors = sorted(C.MAJOR_SOCCER_LEAGUES)
    marks = ",".join("?" * len(majors))
    league_sql = f" AND league IN ({marks})" if majors_only else ""
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        while time.time() < deadline:
            todo = reg.execute(
                f"""SELECT * FROM ou05_markets WHERE hist_status IS NULL{league_sql}
                    ORDER BY CASE WHEN league IN ({marks}) THEN 0 ELSE 1 END, closed DESC, game_start DESC LIMIT ?""",
                (*(majors if majors_only else []), *majors, batch)).fetchall()
            if not todo:
                break
            jobs, windows = [], {}
            for m in todo:
                w = _window(paths, m, ts)
                if w is None:
                    with dbmod.tx(reg):
                        reg.execute("UPDATE ou05_markets SET hist_status='empty', hist_rows=0, hist_updated_at=? "
                                    "WHERE condition_id=?", (ts, m["condition_id"]))
                    continue
                windows[m["condition_id"]] = (m, w)
                jobs.append((m["condition_id"], m["over_token"], m["under_token"], w[0], w[1], client))
            for cid, hist, err in pool.map(_fetch, jobs):
                m, (lo, hi) = windows[cid]
                if err is not None:
                    log.warning("history %s: %r", cid, err)
                    with dbmod.tx(reg):
                        reg.execute("UPDATE ou05_markets SET hist_status='failed', hist_updated_at=? WHERE condition_id=?",
                                    (ts, cid))
                        store.quality(reg, "ou05_history_failed", cid, error=repr(err)[:300])
                    stats["markets_failed"] += 1
                    continue
                op, up = hist.get(m["over_token"], []), hist.get(m["under_token"], [])
                stats["points"] += len(op) + len(up)
                rows = history_quote_rows(cid, op, up, m["game_start"])
                n = store.write_quotes(paths, rows)
                stats["rows"] += n
                # open markets: history up to the first poll row (or now); the poll series continues from there
                with dbmod.tx(reg):
                    reg.execute("UPDATE ou05_markets SET hist_status=?, hist_rows=?, hist_until=?, hist_updated_at=? "
                                "WHERE condition_id=?", ("done" if rows else "empty", n, hi, ts, cid))
                stats["markets_done" if rows else "markets_empty"] += 1
    return stats


def run(paths, client: Client, ts: int, budget_s: float, since: int, workers: int = 4,
        majors_only: bool = False) -> dict:
    reg = store.registry(paths)
    try:
        with dbmod.tx(reg):     # one more try per run for failed fetches
            reg.execute("UPDATE ou05_markets SET hist_status=NULL WHERE hist_status='failed'")
        t0 = time.time()
        deadline = t0 + budget_s
        enum = enumerate_closed(reg, since, client, t0 + budget_s / 2)
        stats = process(paths, reg, client, ts, deadline, workers=workers, majors_only=majors_only)
        remaining = {r[0]: r[1] for r in reg.execute(
            "SELECT CASE WHEN league IN (%s) THEN 'major' ELSE 'other' END, COUNT(*) FROM ou05_markets "
            "WHERE hist_status IS NULL GROUP BY 1" % ",".join("?" * len(C.MAJOR_SOCCER_LEAGUES)),
            sorted(C.MAJOR_SOCCER_LEAGUES))}
        done_total = reg.execute("SELECT COUNT(*) FROM ou05_markets WHERE hist_status IN ('done','empty')").fetchone()[0]
    finally:
        reg.close()
    return {"enumeration": {k: enum.get(k) for k in ("pages", "staged", "done")}, **stats,
            "remaining": remaining, "done_total": done_total, "secs": round(time.time() - t0, 1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab ou05 backfill", description="O/U 0.5 mid-price history (resumable)")
    ap.add_argument("--budget-minutes", type=float, default=10.0)
    ap.add_argument("--since", default="2026-02-01")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--majors-only", action="store_true", help="only MAJOR_SOCCER_LEAGUES in phase B")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from polylab.settings import paths
    p = paths()
    since = int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    reg = store.registry(p)
    run_id = store.job_start(reg, "ou05-backfill")
    client = default_client()
    try:
        out = run(p, client, C.now(), args.budget_minutes * 60, since, workers=args.workers,
                  majors_only=args.majors_only)
    except Exception as exc:
        store.job_finish(reg, run_id, False, {"error": repr(exc)[:500]})
        raise
    out["http_calls"] = client.calls
    out["http_mb"] = round(client.bytes_in / 1e6, 1)
    store.job_finish(reg, run_id, True, out)
    reg.close()
    print(json.dumps(out, indent=1, default=str))
    return 0
