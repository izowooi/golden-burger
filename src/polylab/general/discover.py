"""`polylab general discover [--min-volume 5000]` (every 10 min) — register open near-resolution markets of every
category, refresh volume/liquidity, mark closings and resolutions.

Sources (probed 2026-10-05 from the Mac mini):
1. Gamma `GET /markets/keyset?closed=false&include_tag=true&volume_num_min=<floor>&end_date_min=<now-1d>
   &end_date_max=<now+12d>` walked by cursor. The listing window is wider than the 4-day horizon because game markets
   carry endDate = kickoff+7d; rows are kept when `store.end_ref` lies in [now-1d, now+4d] (~1.7k markets with
   volume >= 1k, ~730 with >= 5k on 2026-10-05; 79 % of closed 5k+ markets of a week were sports).
2. Closing: registered open markets missing from the open listing -> Gamma `condition_ids=..&closed=true`; winner
   from outcomePrices + umaResolutionStatus resolved (common.gamma_winner), else Data API `/v2/resolutions`.
Writes data/general/registry.db only (gen_markets upsert, gen_metrics volume/liquidity per run). Markets that core.db
already tracks are registered with in_core=1 (category/end_ref for cherry) but polled by core, not here.
"""

from __future__ import annotations

import json
import logging
import time

from polylab import db as dbmod
from polylab.api import data_api, gamma
from polylab.api.http import Client, default_client
from polylab.collector import common as C
from polylab.general import store

log = logging.getLogger("polylab.general.discover")

DEFAULT_MIN_VOLUME = 5000.0
LIST_BEFORE_S = 86400
LIST_AHEAD_S = 12 * 86400
RESOLVE_RETRY_S = 14 * 86400


def tag_slugs(m: dict) -> list[str]:
    out = []
    for t in m.get("tags") or []:
        slug = t.get("slug") if isinstance(t, dict) else t
        if slug:
            out.append(str(slug).lower())
    return out


def fee_json(m: dict) -> str | None:
    keys = {k: m.get(k) for k in ("feesEnabled", "feeSchedule", "feeType") if m.get(k) is not None}
    return json.dumps(keys, separators=(",", ":")) if keys else None


def market_row(m: dict, *, ts: int, source: str) -> dict | None:
    """Registry row from a Gamma market; None unless it has exactly two outcome tokens."""
    toks = [str(t) for t in gamma.json_list(m.get("clobTokenIds"))]
    if len(toks) != 2 or not m.get("conditionId"):
        return None
    ev = (m.get("events") or [{}])[0]
    tags = tag_slugs(m)
    game_start = gamma.parse_time(m.get("gameStartTime"))
    end_date = gamma.parse_time(m.get("endDate"))
    closed = 1 if m.get("closed") else 0
    winner = C.gamma_winner(m) if closed else None
    return {
        "condition_id": m["conditionId"],
        "market_id": str(m["id"]) if m.get("id") is not None else None,
        "event_id": str(ev["id"]) if ev.get("id") is not None else None,
        "event_slug": ev.get("slug"),
        "question": m.get("question"), "slug": m.get("slug"),
        "category": store.category(tags, m.get("feeType")),
        "tags": json.dumps(tags, separators=(",", ":")),
        "outcomes": json.dumps([str(x) for x in gamma.json_list(m.get("outcomes"))], separators=(",", ":")),
        "yes_token": toks[0], "no_token": toks[1],
        "neg_risk": 1 if m.get("negRisk") else 0,
        "sports_market_type": m.get("sportsMarketType"),
        "created_at": gamma.parse_time(m.get("createdAt")),
        "start_date": gamma.parse_time(m.get("startDate")),
        "end_date": end_date, "game_start": game_start, "end_ref": store.end_ref(game_start, end_date),
        "closed": closed,
        "closed_at": gamma.parse_time(m.get("closedTime")) if closed else None,
        "resolved_index": winner,
        "resolution_source": "gamma_outcome_prices" if winner is not None else None,
        "fee_type": m.get("feeType"), "fee_json": fee_json(m),
        "volume": C.market_volume(m),
        "liquidity": C._float(m.get("liquidityNum", m.get("liquidity"))),
        "ts": ts, "source": source,
    }


UPSERT = """
INSERT INTO gen_markets(condition_id, market_id, event_id, event_slug, question, slug, category, tags, outcomes,
                        yes_token, no_token, neg_risk, sports_market_type, created_at, start_date, end_date, game_start,
                        end_ref, closed, closed_at, resolved_index, resolution_source, fee_type, fee_json, volume,
                        liquidity, in_core, first_seen, updated_at, source)
VALUES(:condition_id, :market_id, :event_id, :event_slug, :question, :slug, :category, :tags, :outcomes,
       :yes_token, :no_token, :neg_risk, :sports_market_type, :created_at, :start_date, :end_date, :game_start,
       :end_ref, :closed, :closed_at, :resolved_index, :resolution_source, :fee_type, :fee_json, :volume,
       :liquidity, :in_core, :ts, :ts, :source)
ON CONFLICT(condition_id) DO UPDATE SET
    market_id=COALESCE(excluded.market_id, gen_markets.market_id),
    event_id=COALESCE(excluded.event_id, gen_markets.event_id),
    event_slug=COALESCE(excluded.event_slug, gen_markets.event_slug),
    question=COALESCE(excluded.question, gen_markets.question),
    slug=COALESCE(excluded.slug, gen_markets.slug),
    category=excluded.category,
    tags=COALESCE(excluded.tags, gen_markets.tags),
    outcomes=COALESCE(excluded.outcomes, gen_markets.outcomes),
    neg_risk=COALESCE(excluded.neg_risk, gen_markets.neg_risk),
    created_at=COALESCE(gen_markets.created_at, excluded.created_at),
    start_date=COALESCE(gen_markets.start_date, excluded.start_date),
    end_date=COALESCE(excluded.end_date, gen_markets.end_date),
    game_start=COALESCE(excluded.game_start, gen_markets.game_start),
    end_ref=COALESCE(excluded.end_ref, gen_markets.end_ref),
    closed=MAX(gen_markets.closed, excluded.closed),
    closed_at=COALESCE(gen_markets.closed_at, excluded.closed_at),
    resolved_index=COALESCE(gen_markets.resolved_index, excluded.resolved_index),
    resolution_source=COALESCE(gen_markets.resolution_source, excluded.resolution_source),
    fee_type=COALESCE(excluded.fee_type, gen_markets.fee_type),
    fee_json=CASE WHEN excluded.closed=1 THEN COALESCE(gen_markets.fee_json, excluded.fee_json)
                  ELSE COALESCE(excluded.fee_json, gen_markets.fee_json) END,
    volume=COALESCE(excluded.volume, gen_markets.volume),
    liquidity=COALESCE(excluded.liquidity, gen_markets.liquidity),
    in_core=MAX(gen_markets.in_core, excluded.in_core),
    updated_at=excluded.updated_at
"""


def upsert_rows(conn, paths, rows: list[dict]) -> None:
    """Upsert with in_core resolved against core.db. Closed rows keep the fee schedule seen while open
    (Gamma reports feesEnabled=false and no schedule after close)."""
    core = store.core_condition_ids(paths, [r["condition_id"] for r in rows])
    for r in rows:
        conn.execute(UPSERT, {**r, "in_core": 1 if r["condition_id"] in core else 0})


def in_horizon(row: dict, ts: int) -> bool:
    e = row.get("end_ref")
    return e is not None and ts - LIST_BEFORE_S <= e <= ts + store.WINDOW_BEFORE_END_S


def discover(conn, paths, client: Client | None = None, ts: int | None = None,
             min_volume: float = DEFAULT_MIN_VOLUME) -> dict:
    client = client or default_client()
    ts = ts or C.now()
    t0 = time.time()
    listed = 0
    rows: dict[str, dict] = {}
    params = {"closed": "false", "include_tag": "true", "volume_num_min": min_volume,
              "end_date_min": gamma.iso(ts - LIST_BEFORE_S), "end_date_max": gamma.iso(ts + LIST_AHEAD_S)}
    for page, _ in gamma.markets_keyset_pages(params, client):
        listed += len(page)
        for m in page:
            if m.get("closed"):
                continue
            row = market_row(m, ts=ts, source="discover")
            if row is not None and in_horizon(row, ts):
                rows[row["condition_id"]] = row
    known = {r[0] for r in conn.execute("SELECT condition_id FROM gen_markets")}
    with dbmod.tx(conn):
        upsert_rows(conn, paths, list(rows.values()))
        ids = {r[0]: r[1] for r in conn.execute(
            "SELECT condition_id, id FROM gen_markets WHERE closed=0 AND end_ref >= ?", (ts - LIST_BEFORE_S,))}
        conn.executemany("INSERT OR REPLACE INTO gen_metrics(id, ts, volume, liquidity) VALUES(?,?,?,?)",
                         [(ids[c], C.minute(ts), r["volume"], r["liquidity"]) for c, r in rows.items() if c in ids])
    cats: dict[str, int] = {}
    for r in rows.values():
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    out = {"listed": listed, "in_horizon": len(rows), "new": len(set(rows) - known), "by_category": cats,
           "list_secs": round(time.time() - t0, 1)}
    out["closing"] = refresh_closed(conn, set(rows), client, ts)
    out["open_registered"] = conn.execute("SELECT COUNT(*) FROM gen_markets WHERE closed=0").fetchone()[0]
    out["secs"] = round(time.time() - t0, 1)
    return out


def refresh_closed(conn, open_ids: set[str], client: Client, ts: int, limit: int = 2000) -> dict:
    """Registered open markets whose end_ref passed or that left the listing, plus closed-unresolved ones."""
    cands = [r[0] for r in conn.execute(        # past end_ref: still listed open = awaiting resolution, skip
        "SELECT condition_id FROM gen_markets WHERE closed=0 AND (end_ref IS NULL OR end_ref <= ?) "
        "ORDER BY end_ref LIMIT ?", (ts + 3600, limit)) if r[0] not in open_ids]
    gone = [r[0] for r in conn.execute("SELECT condition_id FROM gen_markets WHERE closed=0 AND end_ref > ?",
                                       (ts + 3600,)) if r[0] not in open_ids]
    unresolved = [r[0] for r in conn.execute(
        "SELECT condition_id FROM gen_markets WHERE closed=1 AND resolved_index IS NULL AND source='discover' "
        "AND COALESCE(closed_at, end_ref, 0) >= ? LIMIT ?", (ts - RESOLVE_RETRY_S, limit))]
    cids = list(dict.fromkeys(cands + gone + unresolved))
    out = {"checked": len(cids), "closed": 0, "resolved": 0, "not_found": 0}
    if not cids:
        return out
    closed = {m["conditionId"]: m for m in gamma.markets_by_condition_ids(cids, closed=True, client=client)
              if m.get("conditionId")}
    need: list[str] = []
    with dbmod.tx(conn):
        for cid in cids:
            m = closed.get(cid)
            if m is None:
                out["not_found"] += 1
                continue
            cur = conn.execute("UPDATE gen_markets SET closed=1, closed_at=COALESCE(closed_at, ?), volume=?, "
                               "updated_at=? WHERE condition_id=? AND closed=0",
                               (gamma.parse_time(m.get("closedTime")) or ts, C.market_volume(m), ts, cid))
            out["closed"] += cur.rowcount
            w = C.gamma_winner(m)
            if w is None:
                need.append(cid)
            else:
                out["resolved"] += set_resolved(conn, cid, w, "gamma_outcome_prices", ts)
    if need:
        try:
            res = data_api.resolutions(need, client)
        except Exception as exc:  # noqa: BLE001 — resolution can wait for the next run
            log.warning("resolutions fallback failed: %r", exc)
            res = {}
        with dbmod.tx(conn):
            for cid, row in res.items():
                w = data_api.winner_from_resolution(row)
                if w is not None:
                    out["resolved"] += set_resolved(conn, cid, w, "data_api_v2", ts)
    return out


def set_resolved(conn, cid: str, index: int, source: str, ts: int) -> int:
    return conn.execute("UPDATE gen_markets SET resolved_index=?, resolution_source=?, updated_at=? "
                        "WHERE condition_id=? AND resolved_index IS NULL", (index, source, ts, cid)).rowcount


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="polylab general discover", description=__doc__.split("\n")[0])
    ap.add_argument("--min-volume", type=float, default=DEFAULT_MIN_VOLUME)
    args = ap.parse_args(argv)
    from polylab.settings import paths
    p = paths()
    conn = store.registry(p)
    run_id = store.job_start(conn, "general-discover")
    try:
        out = discover(conn, p, min_volume=args.min_volume)
    except Exception as exc:
        store.job_finish(conn, run_id, False, {"error": repr(exc)[:500]})
        raise
    out["http_calls"] = default_client().calls
    store.job_finish(conn, run_id, True, out)
    conn.close()
    print(json.dumps(out))
    return 0
