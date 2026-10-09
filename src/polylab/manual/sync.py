"""`polylab manual sync`: pull each watch address's public Data API v2 activity + positions into its ledger.

Sources (docs/research/api-sources.md, probed 2026-10-03):
- `/v2/activity?user&start&limit<=1000&cursor&exclude_deposits_withdrawals=false` -> TRADE (BUY/SELL, maker and
  taker), REDEEM (with token_id, usdc_size = cash paid out), SPLIT/MERGE/CONVERSION, DEPOSIT/WITHDRAWAL,
  YIELD, MAKER/TAKER_REBATE. `start` is honoured with `user`. `/v2/trades?user` is NOT used: it defaults to
  taker-only and dropped ~70% of one wallet's fills.
- `/v2/positions?user` -> current_price marks for open positions.
- links: core.db (read-only) by condition_id, else Gamma `/markets/keyset?condition_ids&closed=<bool>`;
  resolution: core resolved index -> `/v2/resolutions` payouts -> Gamma closed+resolved outcomePrices.
The address only goes into request params; it is never logged, stored or returned.
"""

from __future__ import annotations

import sys
import time

from polylab import settings
from polylab.analysis import _common as C
from polylab.api import data_api, gamma
from polylab.api.http import DATA_URL, ApiError, Client, default_client
from polylab.collector import common as CC
from polylab.manual import ledger
from polylab.manual import predictions as predictions_mod

ACTIVITY_PAGE = 1000
POSITIONS_PAGE = 500
MAX_PAGES = 200
REFETCH_LOOKBACK_S = 86400      # activity types are indexed by different pipelines: re-read the last day
US_SPORTS = ("mlb", "nba", "nfl", "nhl")
SOCCER_SMT = ("totals", "both_teams_to_score", "soccer_team_totals")
SOCCER_LEAGUE_HINTS = frozenset({"fif", "fifwc", "unl", "wcq", "uefa", "euro", "copa"})


def _paged(path: str, params: dict, bucket: str, client: Client) -> list[dict]:
    out, cursor = [], None
    for _ in range(MAX_PAGES):
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        body = client.get(DATA_URL + path, bucket=bucket, params=p) or {}
        rows = body.get("data") or []
        out.extend(r for r in rows if isinstance(r, dict))
        cursor = (body.get("pagination") or {}).get("next_cursor")
        if not cursor or not rows:
            return out
    raise ApiError(None, path, f"more than {MAX_PAGES} pages")


def fetch_activity(address: str, start: int, client: Client) -> list[dict]:
    return _paged("/v2/activity", {"user": address, "start": max(int(start), 1), "limit": ACTIVITY_PAGE,
                                   "exclude_deposits_withdrawals": "false"}, "data", client)


def fetch_positions(address: str, client: Client) -> list[dict]:
    return _paged("/v2/positions", {"user": address, "limit": POSITIONS_PAGE}, "data", client)


# ------------------------------------------------------------------ links

def _sport(league: str | None, smt: str | None, market: dict) -> str | None:
    """Best effort for markets outside core.db: US codes by league, soccer by market shape/league hints."""
    if league in US_SPORTS:
        return league
    if league and (league in CC.MAJOR_SOCCER_LEAGUES or league in SOCCER_LEAGUE_HINTS):
        return "soccer"
    if smt in SOCCER_SMT or (smt == "moneyline" and CC.is_soccer_draw(market)):
        return "soccer"
    return None


def core_links(paths, condition_ids: list[str]) -> dict[str, dict]:
    conn = C.open_ro(paths.core_db)
    if conn is None or not condition_ids:
        return {}
    out = {}
    try:
        for i in range(0, len(condition_ids), 500):
            chunk = condition_ids[i:i + 500]
            rows = conn.execute(
                f"""SELECT m.condition_id, m.game_key, m.market_type, m.line, m.question, m.closed,
                           m.resolved_outcome_index, m.resolved_at, g.sport, g.league, g.title, g.home_team,
                           g.away_team, g.start_time
                    FROM markets m LEFT JOIN games g ON g.game_key = m.game_key
                    WHERE m.condition_id IN ({','.join('?' * len(chunk))})""", chunk).fetchall()
            for r in rows:
                toks = {t["token_id"]: {"index": t["outcome_index"], "label": t["outcome_label"], "side": t["side"]}
                        for t in conn.execute("SELECT token_id, outcome_index, outcome_label, side FROM tokens "
                                              "WHERE condition_id=?", (r["condition_id"],))}
                resolved = r["resolved_outcome_index"] is not None
                out[r["condition_id"]] = {
                    "condition_id": r["condition_id"], "source": "core", "game_key": r["game_key"],
                    "sport": r["sport"], "league": r["league"], "game_title": r["title"], "home_team": r["home_team"],
                    "away_team": r["away_team"], "start_time": r["start_time"], "market_type": r["market_type"],
                    "line": r["line"], "question": r["question"], "tokens": toks, "closed": r["closed"],
                    "winner_index": r["resolved_outcome_index"], "payouts": None,
                    "resolved_at": r["resolved_at"], "resolution_source": "core" if resolved else None}
    finally:
        conn.close()
    return out


def gamma_link(m: dict) -> dict:
    ev = (m.get("events") or [{}])[0] or {}
    league = CC.league_code(ev, m)
    smt = m.get("sportsMarketType")
    sport = _sport(league, smt, m)
    title = (ev.get("title") or "").split(" - ")[0].strip() or None
    home, away = CC.split_title(title)
    start = gamma.parse_time(m.get("gameStartTime")) or gamma.parse_time(ev.get("startTime"))
    labels = gamma.json_list(m.get("outcomes"))
    toks = {}
    for i, tid in enumerate(gamma.json_list(m.get("clobTokenIds"))):
        label = labels[i] if i < len(labels) else None
        low = (label or "").strip().lower()
        toks[str(tid)] = {"index": i, "label": label,
                          "side": low if low in ("over", "under", "yes", "no") else None}
    winner = CC.gamma_winner(m)
    return {"condition_id": m.get("conditionId"), "source": "gamma", "game_key": str(ev["id"]) if ev.get("id") else None,
            "sport": sport, "league": league, "game_title": title if start and home else (title or m.get("question")),
            "home_team": home, "away_team": away, "start_time": start if home else None,
            "market_type": CC.market_type_of(m, sport), "line": _f(m.get("line")), "question": m.get("question"),
            "tokens": toks, "closed": 1 if m.get("closed") else 0, "winner_index": winner, "payouts": None,
            "resolved_at": gamma.parse_time(m.get("closedTime")) if winner is not None else None,
            "resolution_source": "gamma" if winner is not None else None}


def _f(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def activity_link(conn, condition_id: str) -> dict:
    r = conn.execute("SELECT title, slug, outcome FROM activity WHERE condition_id=? ORDER BY ts LIMIT 1",
                     (condition_id,)).fetchone()
    return {"condition_id": condition_id, "source": "activity", "game_title": r["title"] if r else None,
            "question": r["title"] if r else None, "tokens": {}, "closed": None}


def apply_resolutions(link: dict, res: dict | None) -> dict:
    """Overlay a /v2/resolutions row: payouts (1e6 units) -> per-outcome fractions; never guess on 50-50 gaps."""
    if not res or res.get("status") != "resolved" or link.get("winner_index") is not None and link.get("payouts"):
        return link
    payouts = res.get("payouts")
    if isinstance(payouts, list) and payouts:
        try:
            vals = [float(x) for x in payouts]
        except (TypeError, ValueError):
            return link
        total = sum(vals)
        if total > 0:
            link = {**link, "payouts": [round(v / total, 6) for v in vals],
                    "winner_index": data_api.winner_from_resolution(res),
                    "resolved_at": gamma.parse_time(res.get("resolved_at") or res.get("last_update_timestamp")),
                    "resolution_source": "data_api", "closed": 1}
    return link


def refresh_links(conn, paths, client: Client, now: int, network: bool = True) -> dict:
    """Link every traded condition; re-check resolution only where shares are still held."""
    links = ledger.load_links(conn)
    conds = [r[0] for r in conn.execute("SELECT DISTINCT condition_id FROM activity WHERE condition_id IS NOT NULL")]
    held = {r[0] for r in conn.execute("SELECT condition_id FROM positions WHERE status='open'")}
    todo = [c for c in conds if c not in links or links[c].get("source") == "activity"
            or (c in held and links[c].get("winner_index") is None and not links[c].get("payouts"))]
    stats = {"core": 0, "gamma": 0, "activity": 0, "resolved": 0}
    if not todo:
        return stats
    found = core_links(paths, todo)
    missing = [c for c in todo if c not in found or found[c].get("game_title") is None]
    if network and missing:
        try:
            for closed in (True, False):
                for m in gamma.markets_by_condition_ids(missing, closed=closed, client=client):
                    if m.get("conditionId") in missing and m["conditionId"] not in found:
                        found[m["conditionId"]] = gamma_link(m)
        except ApiError as exc:
            print(f"manual: gamma lookup failed ({exc.status})", file=sys.stderr)
    for c in todo:
        link = found.get(c) or links.get(c) or activity_link(conn, c)
        found[c] = link
    unresolved = [c for c in todo if found[c].get("winner_index") is None and not found[c].get("payouts")]
    if network and unresolved:
        try:
            res = data_api.resolutions(unresolved, client=client)
        except ApiError as exc:
            print(f"manual: resolutions lookup failed ({exc.status})", file=sys.stderr)
            res = {}
        for c in unresolved:
            found[c] = apply_resolutions(found[c], res.get(c))
    for c in todo:
        ledger.upsert_link(conn, found[c], now)
        stats[found[c]["source"]] = stats.get(found[c]["source"], 0) + 1
        stats["resolved"] += 1 if (found[c].get("winner_index") is not None or found[c].get("payouts")) else 0
    conn.commit()
    return stats


# ------------------------------------------------------------------ per account

def bot_activity(paths, alias: str, variants=None) -> tuple[set[tuple[str, str]], set[str]]:
    """((tx_hash, token_id) of every live fill, live condition_ids) in the strategy ledgers of the polylab variants
    whose account is this watch alias. Raises when the registry cannot be read: without it the bot's trades would
    be counted as the owner's manual bets."""
    import json  # noqa: PLC0415
    import sqlite3  # noqa: PLC0415

    from polylab import registry  # noqa: PLC0415
    variants = registry.load_all(include_off=True) if variants is None else variants
    trades: set[tuple[str, str]] = set()
    conditions: set[str] = set()
    for v in variants:
        path = paths.strategy_db(v.id)
        if v.account != alias or not path.exists():
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            for token, raw in conn.execute("SELECT o.token_id, f.raw FROM fills f JOIN orders o USING(intent_id) "
                                           "WHERE o.mode='live' AND f.raw IS NOT NULL"):
                try:
                    tx = (json.loads(raw) or {}).get("transaction_hash")
                except (TypeError, ValueError):
                    tx = None
                if tx:
                    trades.add((str(tx).lower(), token))
            conditions |= {r[0] for r in conn.execute("SELECT DISTINCT condition_id FROM positions WHERE mode='live'")}
        finally:
            conn.close()
    return trades, conditions


def sync_account(acct: settings.WatchAccount, paths, client: Client | None = None, now: int | None = None,
                 since: int | None = None, full: bool = False) -> dict:
    client = client or default_client()
    now = now or int(time.time())
    since = since if since is not None else acct.since
    conn = ledger.open_db(ledger.db_path(paths, acct.alias))
    try:
        ledger.set_meta(conn, "alias", acct.alias)
        ledger.set_meta(conn, "label", acct.label)
        ledger.set_meta(conn, "markets", ",".join(acct.markets) if acct.markets else "")
        ledger.set_meta(conn, "since", since)
        if acct.bankroll_usdc is not None and ledger.get_meta(conn, "bankroll_usdc") is None:
            ledger.set_meta(conn, "bankroll_usdc", acct.bankroll_usdc)        # frozen at first sight
            ledger.set_meta(conn, "bankroll_first_seen_at", now)
        last = ledger.last_activity_ts(conn)
        start = (since or 1) if full or last is None else max(last - REFETCH_LOOKBACK_S, since or 1)
        rows = fetch_activity(acct.address, start, client)
        new = ledger.ingest_activity(conn, rows, now)
        conn.commit()
        ledger.store_api_positions(conn, fetch_positions(acct.address, client), now)
        conn.commit()
        bot = bot_activity(paths, acct.alias)
        ledger.rebuild(conn, acct.alias, since, now, bot)   # first pass: know which positions are still open
        links = refresh_links(conn, paths, client, now)
        counts = ledger.rebuild(conn, acct.alias, since, now, bot)
        ledger.set_meta(conn, "last_sync_at", now)
        conn.commit()
        return {"alias": acct.alias, "fetched": len(rows), "new": new, "links": links, "positions": counts}
    finally:
        conn.close()


def run(paths, accounts: list | None = None, client: Client | None = None, now: int | None = None,
        since: int | None = None, full: bool = False, only: str | None = None) -> list[dict]:
    accounts = settings.watch_accounts() if accounts is None else accounts
    out = []
    for acct in accounts:
        if only and acct.alias != only:
            continue
        try:
            out.append(sync_account(acct, paths, client=client, now=now, since=since, full=full))
        except ApiError as exc:          # status only: the URL carries the address
            out.append({"alias": acct.alias, "error": f"api http {exc.status}"})
        except Exception as exc:  # noqa: BLE001
            out.append({"alias": acct.alias, "error": type(exc).__name__})
    try:
        out.append({"predictions": predictions_mod.sync(paths)})
    except Exception as exc:  # noqa: BLE001
        out.append({"predictions": {"error": type(exc).__name__}})
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="polylab manual sync")
    ap.add_argument("--alias", help="only this watch alias")
    ap.add_argument("--since", help="YYYY-MM-DD / Nd / unix (overrides WATCH_<ALIAS>__SINCE for this run)")
    ap.add_argument("--full", action="store_true", help="refetch from SINCE instead of the last stored activity")
    args = ap.parse_args(argv)
    try:
        paths = settings.paths()
    except settings.StorageUnavailable as exc:
        print(f"manual: {exc}", file=sys.stderr)
        return 3
    accounts = settings.watch_accounts()
    if not accounts:
        print("manual: no watch accounts (~/.polylab/watch.env WATCH_<ALIAS>__ADDRESS=...)")
        return 0
    results = run(paths, accounts, since=C.parse_since(args.since), full=args.full, only=args.alias)
    failed = 0
    for r in results:
        if "predictions" in r:
            print(f"manual: predictions {r['predictions']}")
        elif r.get("error"):
            failed += 1
            print(f"manual: {r['alias']}: FAILED {r['error']}", file=sys.stderr)
        else:
            pos = {k: v for k, v in r["positions"].items() if v}
            print(f"manual: {r['alias']}: activity fetched {r['fetched']} new {r['new']} · links {r['links']} · "
                  f"positions {pos}")
    return 1 if failed else 0
