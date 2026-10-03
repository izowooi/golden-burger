"""Manual ledger DB: raw activity + market links -> deterministic positions/fills (pure, no network).

Accounting (cash based, so it is exact whatever the fee attribution):
- cost     = sum of BUY `usdc_size` (Data API includes the taker fee on buys: 800 @ 0.95 -> 761.9)
- proceeds = sum of SELL `usdc_size` (net of fee) + REDEEM cash, or remaining shares x payout once the market
             resolution is confirmed (core.db resolved index, /v2/resolutions payouts, Gamma closed+resolved)
- realized_pnl = proceeds - cost, only when the position is fully settled; otherwise NULL.
- fee_usdc per fill = |size x price - usdc_size| when the sign is plausible, else NULL (unknown, never 0).
One position per (alias, token). SPLIT/MERGE/CONVERSION touch a whole condition and are not modelled:
positions on such a condition are quarantined and excluded from P&L (counted, not zeroed). A SELL or REDEEM
without the BUY inside the ingest window (SINCE) is quarantined too.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from polylab.db import STRATEGY_SCHEMA, connect, now as db_now, tx

EPS_SHARES = 1e-6
FEE_TOL = 1e-4
CONDITION_EVENTS = ("SPLIT", "MERGE", "CONVERSION")
CASH_FLOWS = ("DEPOSIT", "WITHDRAWAL", "YIELD", "MAKER_REBATE", "TAKER_REBATE", "REWARD", "REFERRAL_REWARD",
              "TIP", "MIGRATION")
WIN_RESULTS = ("resolved_win", "redeemed")
RESULTS = ("open", "closed_sell", "resolved_win", "resolved_loss", "resolved_split", "redeemed", "quarantined")
DROP_KEYS = ("proxy_wallet", "name", "pseudonym", "bio", "profile_image", "profile_image_optimized", "icon")

MANUAL_SCHEMA = STRATEGY_SCHEMA + (
    """
    CREATE TABLE IF NOT EXISTS activity (
        uid TEXT PRIMARY KEY,                   -- digest of the row + ordinal among identical rows
        ts INTEGER NOT NULL,
        type TEXT NOT NULL,                     -- TRADE | REDEEM | SPLIT | MERGE | DEPOSIT | ...
        side TEXT,
        condition_id TEXT,
        token_id TEXT,
        outcome_index INTEGER,
        outcome TEXT,
        size REAL,
        usdc_size REAL,
        price REAL,
        tx_hash TEXT,
        title TEXT,
        slug TEXT,
        event_slug TEXT,
        raw TEXT,                               -- json without wallet/profile fields
        ingested_at INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS activity_ts ON activity(ts)",
    """
    CREATE TABLE IF NOT EXISTS links (
        condition_id TEXT PRIMARY KEY,
        source TEXT NOT NULL,                   -- core | gamma | activity
        game_key TEXT,                          -- core.db game_key or Gamma event id
        sport TEXT,                             -- soccer | mlb | nba | nfl | nhl | NULL (not a tracked sport)
        league TEXT,
        game_title TEXT,
        home_team TEXT,
        away_team TEXT,
        start_time INTEGER,
        market_type TEXT,                       -- moneyline | draw | total | spread | btts | team_to_score | NULL
        line REAL,
        question TEXT,
        tokens TEXT,                            -- json {token_id: {"index", "label", "side"}}
        closed INTEGER,
        winner_index INTEGER,
        payouts TEXT,                           -- json list of payout fractions per outcome index
        resolved_at INTEGER,
        resolution_source TEXT,                 -- core | data_api | gamma
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_positions (
        token_id TEXT PRIMARY KEY,              -- /v2/positions snapshot (marks for open positions)
        condition_id TEXT,
        status TEXT,
        current_size REAL,
        avg_price REAL,
        current_price REAL,
        current_value REAL,
        total_cost_usdc REAL,
        entry_fees_usdc REAL,
        realized_pnl REAL,
        unrealized_pnl REAL,
        fetched_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS position_meta (
        position_id TEXT PRIMARY KEY,
        alias TEXT NOT NULL,
        result TEXT NOT NULL,                   -- see RESULTS
        track2 INTEGER NOT NULL,                -- 1 = linked to a sports game (Track 2 scope)
        game_title TEXT,
        start_time INTEGER,
        market_type TEXT,
        line REAL,
        side TEXT,                              -- over | under | home | away | draw | yes | no | NULL
        link_source TEXT,
        implied_p00 REAL,                       -- market P(0-0) at entry for total 0.5 bets
        bought_shares REAL,
        remaining_shares REAL,
        sell_proceeds_usdc REAL,
        payout_usdc REAL,
        payout_source TEXT,                     -- redeem | core | data_api | gamma
        mark_price REAL,
        unrealized_pnl REAL,
        quarantine_reason TEXT,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at INTEGER NOT NULL
    )
    """,
)


def manual_dir(paths) -> Path:
    return Path(paths.data) / "manual"


def db_path(paths, alias: str) -> Path:
    return manual_dir(paths) / f"{alias}.db"


def open_db(path: Path) -> sqlite3.Connection:
    return connect(path, MANUAL_SCHEMA)


def set_meta(conn, key: str, value) -> None:
    conn.execute("INSERT INTO meta(key, value, updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
                 "value=excluded.value, updated_at=excluded.updated_at",
                 (key, None if value is None else str(value), db_now()))


def get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _f(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    try:
        return None if v is None or v == "" else int(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ activity ingest

def _base_digest(r: dict) -> str:
    key = "|".join(str(r.get(k)) for k in ("type", "side", "transaction_hash", "condition_id", "token_id",
                                            "size", "usdc_size", "price", "timestamp"))
    return hashlib.sha1(key.encode()).hexdigest()


def activity_uids(rows: list[dict]) -> list[str]:
    """Identical rows (same tx/second/size, e.g. two equal fills of one order) get an ordinal suffix.
    Callers refetch from the last stored second inclusive so a same-second group is always numbered whole."""
    seen: dict[str, int] = {}
    out = []
    for r in rows:
        base = _base_digest(r)
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(hashlib.sha1(f"{base}#{n}".encode()).hexdigest()[:32])
    return out


def ingest_activity(conn, rows: list[dict], now: int) -> int:
    """INSERT OR IGNORE; returns new rows. Wallet/profile fields are dropped before storage."""
    rows = [r for r in rows if isinstance(r, dict) and r.get("type") and _i(r.get("timestamp")) is not None]
    new = 0
    for uid, r in zip(activity_uids(rows), rows):
        clean = {k: v for k, v in r.items() if k not in DROP_KEYS}
        cur = conn.execute(
            "INSERT OR IGNORE INTO activity(uid, ts, type, side, condition_id, token_id, outcome_index, outcome, size, "
            "usdc_size, price, tx_hash, title, slug, event_slug, raw, ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, _i(r["timestamp"]), str(r["type"]).upper(), (r.get("side") or None), r.get("condition_id") or None,
             r.get("token_id") or None, _i(r.get("outcome_index")), r.get("outcome") or None, _f(r.get("size")),
             _f(r.get("usdc_size")), _f(r.get("price")), r.get("transaction_hash") or None, r.get("title"),
             r.get("slug"), r.get("event_slug"), json.dumps(clean, sort_keys=True), now))
        new += cur.rowcount
    return new


def last_activity_ts(conn) -> int | None:
    row = conn.execute("SELECT MAX(ts) FROM activity").fetchone()
    return row[0] if row and row[0] is not None else None


def store_api_positions(conn, rows: list[dict], now: int) -> None:
    conn.execute("DELETE FROM api_positions")
    for r in rows:
        if not r.get("token_id"):
            continue
        conn.execute("INSERT OR REPLACE INTO api_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (str(r["token_id"]), r.get("condition_id"), r.get("status"), _f(r.get("current_size")),
                      _f(r.get("avg_price")), _f(r.get("current_price")), _f(r.get("current_value")),
                      _f(r.get("total_cost_usdc")), _f(r.get("entry_fees_usdc")), _f(r.get("realized_pnl")),
                      _f(r.get("unrealized_pnl")), now))


# ------------------------------------------------------------------ links

def upsert_link(conn, link: dict, now: int) -> None:
    cols = ("condition_id", "source", "game_key", "sport", "league", "game_title", "home_team", "away_team",
            "start_time", "market_type", "line", "question", "tokens", "closed", "winner_index", "payouts",
            "resolved_at", "resolution_source")
    vals = [link.get(c) for c in cols]
    vals[cols.index("tokens")] = json.dumps(link.get("tokens") or {}, sort_keys=True)
    vals[cols.index("payouts")] = json.dumps(link["payouts"]) if link.get("payouts") is not None else None
    conn.execute(f"INSERT OR REPLACE INTO links({', '.join(cols)}, updated_at) VALUES({', '.join('?' * len(cols))}, ?)",
                 (*vals, now))


def load_links(conn) -> dict[str, dict]:
    out = {}
    for r in conn.execute("SELECT * FROM links"):
        d = dict(r)
        d["tokens"] = json.loads(d["tokens"] or "{}")
        d["payouts"] = json.loads(d["payouts"]) if d["payouts"] else None
        out[d["condition_id"]] = d
    return out


def payout_fraction(link: dict | None, outcome_index: int | None) -> tuple[float | None, str | None]:
    """(payout per share, source) from a confirmed resolution; (None, None) when unresolved/unknown."""
    if not link or outcome_index is None:
        return None, None
    if link.get("payouts"):
        p = link["payouts"]
        if 0 <= outcome_index < len(p) and p[outcome_index] is not None:
            return float(p[outcome_index]), link.get("resolution_source")
    if link.get("winner_index") is not None:
        return (1.0 if int(link["winner_index"]) == outcome_index else 0.0), link.get("resolution_source")
    return None, None


# ------------------------------------------------------------------ rebuild

def _fill_fee(side: str, size: float | None, price: float | None, usdc: float | None) -> float | None:
    if size is None or price is None or usdc is None:
        return None
    gross = size * price
    fee = usdc - gross if side == "BUY" else gross - usdc
    if fee < -FEE_TOL or fee > max(gross * 0.1, 0.01):
        return None                                     # implausible: unknown, never 0
    return round(max(fee, 0.0), 6)


def _side_of(link: dict | None, token_id: str, outcome: str | None) -> str | None:
    tok = ((link or {}).get("tokens") or {}).get(token_id) or {}
    if tok.get("side"):
        return tok["side"]
    lab = (outcome or tok.get("label") or "").strip().lower()
    return lab if lab in ("over", "under", "yes", "no") else None


def implied_p00(market_type: str | None, line: float | None, side: str | None, entry_price: float | None):
    """Market-implied P(0-0) at entry for total-goals 0.5 bets: Over price = P(at least one goal)."""
    if market_type != "total" or line is None or abs(float(line) - 0.5) > 1e-9 or entry_price is None:
        return None
    if side == "over":
        return round(1.0 - entry_price, 6)
    if side == "under":
        return round(entry_price, 6)
    return None


def rebuild(conn, alias: str, since: int | None, now: int) -> dict:
    """Recompute orders/fills/positions/position_meta from stored activity (idempotent, one transaction)."""
    links = load_links(conn)
    marks = {r["token_id"]: dict(r) for r in conn.execute("SELECT * FROM api_positions")}
    acts = [dict(r) for r in conn.execute("SELECT * FROM activity WHERE ts >= ? ORDER BY ts, uid", (since or 0,))]
    state: dict[str, dict] = {}
    bad_conditions: dict[str, str] = {}
    orphan_redeems: list[dict] = []
    unattributed = 0

    def pos(token_id, r):
        return state.setdefault(token_id, {"token_id": token_id, "condition_id": r.get("condition_id"),
                                           "outcome": r.get("outcome"), "outcome_index": r.get("outcome_index"),
                                           "title": r.get("title"), "buys": [], "sells": [], "redeems": []})
    for r in acts:
        t = r["type"]
        if t == "TRADE" and r.get("token_id") and r.get("side") in ("BUY", "SELL"):
            p = pos(r["token_id"], r)
            p["buys" if r["side"] == "BUY" else "sells"].append(r)
            if p["outcome_index"] is None:
                p["outcome_index"] = r.get("outcome_index")
        elif t == "REDEEM" and r.get("condition_id"):
            if r.get("token_id"):
                pos(r["token_id"], r)["redeems"].append(r)
            else:
                orphan_redeems.append(r)
        elif t in CONDITION_EVENTS and r.get("condition_id"):
            bad_conditions.setdefault(r["condition_id"], t.lower())
    for r in orphan_redeems:          # some v2 REDEEM rows carry no token_id: attribute when unambiguous
        target = _redeem_target(state, r)
        if target is None:
            bad_conditions.setdefault(r["condition_id"], "redeem_without_token")
            unattributed += 1
        else:
            target["redeems"].append(r)
    counts = {r: 0 for r in RESULTS}
    with tx(conn):
        conn.execute("DELETE FROM fills")
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM positions")
        conn.execute("DELETE FROM position_meta")
        for token_id, p in state.items():
            row = _position(alias, p, links.get(p["condition_id"]), marks.get(token_id),
                            bad_conditions.get(p["condition_id"]), now)
            if row is None:
                continue
            position, meta = row
            counts[meta["result"]] += 1
            _write_position(conn, position, meta, p, now)
        set_meta(conn, "unattributed_redeems", unattributed)   # tokenless REDEEM matching no held position
        # Accounts shared with old bots (e.g. red) keep only the owner's chosen market types in Track 2 scope.
        markets = [m for m in (get_meta(conn, "markets") or "").split(",") if m]
        if markets:
            conn.execute(f"UPDATE position_meta SET track2=0 WHERE COALESCE(market_type,'') NOT IN "
                         f"({','.join('?' * len(markets))})", markets)
    return counts


def _redeem_target(state: dict[str, dict], r: dict) -> dict | None:
    """The one held position on the redeemed condition (by outcome_index when several are held)."""
    held = [p for p in state.values() if p["condition_id"] == r["condition_id"]
            and sum(b["size"] or 0 for b in p["buys"]) - sum(s["size"] or 0 for s in p["sells"])
            - sum(x["size"] or 0 for x in p["redeems"]) > EPS_SHARES]
    if len(held) > 1 and r.get("outcome_index") is not None:
        held = [p for p in held if p["outcome_index"] == r["outcome_index"]]
    return held[0] if len(held) == 1 else None


def _position(alias: str, p: dict, link: dict | None, mark: dict | None, bad: str | None, now: int):
    buys, sells, redeems = p["buys"], p["sells"], p["redeems"]
    bought = sum(b["size"] or 0.0 for b in buys)
    sold = sum(s["size"] or 0.0 for s in sells)
    redeemed = sum(r["size"] or 0.0 for r in redeems)
    cost = round(sum(b["usdc_size"] or 0.0 for b in buys), 6)
    sell_cash = round(sum(s["usdc_size"] or 0.0 for s in sells), 6)
    redeem_cash = round(sum(r["usdc_size"] or 0.0 for r in redeems), 6)
    entry_fees = [_fill_fee("BUY", b["size"], b["price"], b["usdc_size"]) for b in buys]
    exit_fees = [_fill_fee("SELL", s["size"], s["price"], s["usdc_size"]) for s in sells]
    entry_price = (sum((b["size"] or 0) * (b["price"] or 0) for b in buys) / bought) if bought > 0 else None
    remaining = bought - sold - redeemed
    link = link or {}
    oi = p["outcome_index"]
    side = _side_of(link, p["token_id"], p["outcome"])
    sport = link.get("sport")
    track2 = 1 if sport and link.get("game_title") and link.get("start_time") else 0
    reason = bad
    if not buys:
        reason = reason or ("sell_without_buy_in_window" if sells else "redeem_without_buy_in_window")
    elif remaining < -max(EPS_SHARES, bought * 1e-6):
        reason = reason or "sold_more_than_bought_in_window"
    position = {"position_id": f"{alias}:{p['token_id']}", "mode": "manual", "param_version": 0,
                "stake_usdc": cost, "sport": sport, "league": link.get("league"), "game_key": link.get("game_key"),
                "condition_id": p["condition_id"], "token_id": p["token_id"], "outcome_label": p["outcome"],
                "opened_at": (buys[0]["ts"] if buys else (sells or redeems)[0]["ts"]),
                "game_minute_at_entry": None, "entry_price": round(entry_price, 6) if entry_price else None,
                "shares": round(max(remaining, 0.0), 6), "cost_usdc": cost,
                "entry_fee_usdc": round(sum(entry_fees), 6) if buys and None not in entry_fees else None,
                "exit_rules": None, "status": "open", "closed_at": None, "exit_reason": None, "exit_price": None,
                "proceeds_usdc": None,
                "exit_fee_usdc": (round(sum(exit_fees), 6) if None not in exit_fees else None) if sells else None,
                "realized_pnl": None, "settlement": None, "redeemed": 1 if redeems else 0, "notes": None}
    meta = {"position_id": position["position_id"], "alias": alias, "result": "open", "track2": track2,
            "game_title": link.get("game_title") or p.get("title"), "start_time": link.get("start_time"),
            "market_type": link.get("market_type"), "line": link.get("line"), "side": side,
            "link_source": link.get("source"),
            "implied_p00": implied_p00(link.get("market_type"), link.get("line"), side, entry_price),
            "bought_shares": round(bought, 6), "remaining_shares": round(remaining, 6),
            "sell_proceeds_usdc": sell_cash, "payout_usdc": None, "payout_source": None, "mark_price": None,
            "unrealized_pnl": None, "quarantine_reason": reason, "updated_at": now}
    if reason:
        position.update(status="quarantined", notes=reason)
        meta["result"] = "quarantined"
        return position, meta
    frac, frac_src = payout_fraction(link, oi)
    last_sell = sells[-1]["ts"] if sells else None
    if redeems and remaining <= max(EPS_SHARES, bought * 1e-6):
        payout, src, closed_at = redeem_cash, "redeem", redeems[-1]["ts"]
        result = "redeemed" if redeem_cash > 0 else "resolved_loss"
    elif remaining <= EPS_SHARES:
        payout, src, closed_at, result = 0.0, None, last_sell, "closed_sell"
    elif frac is not None:
        payout = round(redeem_cash + max(remaining, 0.0) * frac, 6)
        src = frac_src if not redeems else f"redeem+{frac_src}"
        closed_at = link.get("resolved_at") or max([x["ts"] for x in buys + sells + redeems])
        result = "resolved_win" if frac >= 1.0 - 1e-9 else ("resolved_loss" if frac <= 1e-9 else "resolved_split")
    else:
        mp = (mark or {}).get("current_price")
        meta["mark_price"] = mp
        if mp is not None:
            meta["unrealized_pnl"] = round(remaining * mp + sell_cash + redeem_cash - cost, 6)
        return position, meta
    proceeds = round(sell_cash + payout, 6)
    exit_shares = sold + max(remaining, 0.0) + redeemed
    position.update(status="closed" if result == "closed_sell" else "resolved", closed_at=closed_at,
                    exit_reason={"closed_sell": "manual_sell", "resolved_win": "resolution_win", "redeemed":
                                 "resolution_win", "resolved_loss": "resolution_loss",
                                 "resolved_split": "resolution_split"}[result],
                    exit_price=round(proceeds / exit_shares, 6) if exit_shares > 0 else None,
                    proceeds_usdc=proceeds, realized_pnl=round(proceeds - cost, 6),
                    settlement="confirmed_sell" if result == "closed_sell" else "resolution", shares=0.0)
    meta.update(result=result, payout_usdc=payout if result != "closed_sell" else None, payout_source=src)
    return position, meta


def _write_position(conn, position: dict, meta: dict, p: dict, now: int) -> None:
    cols = list(position)
    conn.execute(f"INSERT INTO positions({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                 [position[c] for c in cols])
    mcols = list(meta)
    conn.execute(f"INSERT INTO position_meta({', '.join(mcols)}) VALUES({', '.join('?' * len(mcols))})",
                 [meta[c] for c in mcols])
    for f in p["buys"] + p["sells"]:
        fee = _fill_fee(f["side"], f["size"], f["price"], f["usdc_size"])
        conn.execute("INSERT INTO orders(intent_id, position_id, created_at, mode, side, token_id, condition_id, "
                     "order_type, usdc_amount, shares, limit_price, status, response, updated_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f["uid"], position["position_id"], f["ts"], "manual", f["side"], f["token_id"],
                      f["condition_id"], "MANUAL", f["usdc_size"] if f["side"] == "BUY" else None,
                      f["size"] if f["side"] == "SELL" else None, f["price"], "confirmed",
                      json.dumps({"source": "data_api_v2_activity", "tx": f["tx_hash"]}), now))
        conn.execute("INSERT INTO fills(fill_id, intent_id, ts, side, price, shares, fee_usdc, status, raw) "
                     "VALUES(?,?,?,?,?,?,?,?,?)",
                     (f["uid"], f["uid"], f["ts"], f["side"], f["price"], f["size"], fee, "CONFIRMED",
                      json.dumps({"usdc_size": f["usdc_size"], "fee_source": "usdc_size_minus_size_x_price"
                                  if fee is not None else "unknown"})))
