#!/usr/bin/env python3
"""Read-only, confirmed-execution sports reporting from verified SQLite pins.

The pure order/position/resolution functions have no database or network effects.
Settlement value is distinct from cash redemption and from confirmed SELL P&L.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3

SCHEMA = "sports-confirmed-trade-report-v1"
SPORTS = ("soccer", "mlb", "nfl", "nba", "nhl")
SPORT_LABELS = {"soccer": "축구", "mlb": "MLB", "nfl": "NFL", "nba": "NBA", "nhl": "NHL"}
EPS = Decimal("0.000001")
TERMINAL = {"MATCHED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}
ACTIVE_TRADES = {"HOLDING", "PENDING_BUY", "PENDING_SELL", "QUARANTINED", "UNFILLED"}
ZERO_PROOFS = {"DELAYED_FOK_TERMINAL_ABSENCE_ZERO_FILL", "DELAYED_FOK_CANCEL_ACK_ZERO_FILL",
               "EXACT_GTC_CANCEL_ACK_ZERO_FILL", "EXPIRED_GTC_TERMINAL_ABSENCE_ZERO_FILL"}


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("finite economic number required")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("finite economic number required") from None
    if not result.is_finite():
        raise ValueError("finite economic number required")
    return result


def utc(value, *, naive_utc=False):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        result = datetime.fromtimestamp(float(number(value)), timezone.utc)
    elif isinstance(value, str) and value:
        if re.fullmatch(r"\d{10}(?:\.\d+)?", value):
            result = datetime.fromtimestamp(float(value), timezone.utc)
        else:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp required")
    if result.tzinfo is None:
        if not naive_utc:
            raise ValueError("timestamp must include a timezone")
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def maybe_time(value, *, naive_utc=False):
    try:
        return utc(value, naive_utc=naive_utc)
    except (ValueError, TypeError, OverflowError):
        return None


def iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def kst(value):
    value = maybe_time(value, naive_utc=True)
    return value.astimezone(timezone(timedelta(hours=9))).isoformat() if value else None


def json_value(value, default=None):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else default
    except (ValueError, TypeError):
        return default


def ref(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:16] if value else None


def fee_evidence(fill, *, maker_zero_fee_contract=False):
    amount = fill.get("fee_amount_usdc")
    if amount is not None:
        fee = number(amount)
        if fee < 0:
            raise ValueError("negative fee requires a separate rebate contract")
        return fee, "EXPLICIT_FEE_AMOUNT"
    rate = fill.get("fee_rate_bps")
    if rate is not None and number(rate) == 0:
        return Decimal(0), "EXPLICIT_ZERO_RATE"
    if rate is None and fill.get("liquidity_role") == "MAKER" and maker_zero_fee_contract:
        return Decimal(0), "MAKER_ZERO_NO_BUILDER_CONTRACT"
    return None, "FEE_UNCONFIRMED"


def order_evidence(submission, fills, *, end=None, maker_zero_fee_contract=False, naive_utc=False):
    """Interpret one exact order; current reconciliation and match cutoff differ."""
    if not isinstance(submission, dict) or submission.get("simulation") != 0:
        return {"state": "UNAVAILABLE", "reason": "EXACT_LIVE_SUBMISSION_MISSING", "complete_now": False,
                "confirmed_size": "0", "confirmed_notional": "0", "fee_usdc": None, "fills": []}
    sub = submission
    side = str(sub.get("side") or "").upper()
    errors, accepted, seen = [], [], set()
    if side not in {"BUY", "SELL"} or not sub.get("submission_id") or not sub.get("token_id"):
        errors.append("ORDER_IDENTITY_OR_SIDE_INVALID")
    for fill in fills:
        if fill.get("status") != "CONFIRMED":
            continue
        key = (fill.get("trade_id"), fill.get("bucket_index", 0))
        if key in seen or not key[0]:
            errors.append("DUPLICATE_OR_MISSING_FILL_ID")
            continue
        seen.add(key)
        if (fill.get("submission_id") != sub.get("submission_id")
                or not sub.get("order_id")
                or fill.get("order_id") != sub.get("order_id")
                or fill.get("side") != side or fill.get("domain_error")):
            errors.append("FILL_ORDER_SIDE_OR_DOMAIN_MISMATCH")
            continue
        try:
            size, price = number(fill.get("size")), number(fill.get("price"))
            if size <= 0 or not 0 < price <= 1:
                raise ValueError("invalid fill domain")
            matched = utc(fill.get("matched_at"), naive_utc=naive_utc)
            fee, basis = fee_evidence(fill, maker_zero_fee_contract=maker_zero_fee_contract)
        except (ValueError, TypeError, OverflowError):
            errors.append("FILL_VALUE_FEE_OR_TIME_INVALID")
            continue
        accepted.append({"fill_ref": ref(key), "size": str(size), "price": str(price),
                         "notional": str(size * price), "fee_usdc": str(fee) if fee is not None else None,
                         "fee_basis": basis, "liquidity_role": fill.get("liquidity_role"),
                         "matched_at": iso(matched), "matched_kst": kst(matched),
                         "before_cutoff": end is None or matched < end})
    all_size = sum((number(f["size"]) for f in accepted), Decimal())
    current_status = sub.get("latest_order_status")
    reconciled = (sub.get("needs_reconciliation") == 0 and not sub.get("reconciliation_error")
                  and not sub.get("latest_status_domain_error"))
    try:
        matched_size = number(sub.get("latest_size_matched"))
        size_matches = matched_size >= 0 and abs(all_size - matched_size) <= EPS
    except ValueError:
        matched_size, size_matches = None, False
    terminal = current_status in TERMINAL or sub.get("reconciliation_proof") == "AUTHENTICATED_TOKEN_TRADE_CATALOG_FULL_FILL"
    selected = [f for f in accepted if f["before_cutoff"]]
    size = sum((number(f["size"]) for f in selected), Decimal())
    cash = sum((number(f["notional"]) for f in selected), Decimal())
    fees_complete = bool(selected) and all(f["fee_usdc"] is not None for f in selected)
    fees = sum((number(f["fee_usdc"]) for f in selected), Decimal()) if fees_complete else None
    complete = bool(accepted) and reconciled and terminal and size_matches and not errors
    zero = (not accepted and reconciled and matched_size == 0
            and sub.get("reconciliation_proof") in ZERO_PROOFS)
    no_post = (not accepted and not sub.get("order_id") and sub.get("outcome_resolution") == "NO_ORDER_CREATED"
               and bool(sub.get("outcome_resolution_reason")) and reconciled)
    state = ("CONFIRMED_COMPLETE" if complete else "CONFIRMED_INCOMPLETE" if accepted else
             "PROVEN_ZERO_FILL" if zero or no_post else "UNRESOLVED_NO_CONFIRMED_FILL")
    return {"state": state, "complete_now": complete, "order_ref": ref(sub.get("order_id") or sub.get("submission_id")),
            "response_status": sub.get("response_status"), "error_type": sub.get("error_type"),
            "side": side, "submitted_at": sub.get("submitted_at"), "latest_order_status": current_status,
            "reconciliation_proof": sub.get("reconciliation_proof"), "reconciled_now": reconciled,
            "last_reconciled_at": sub.get("last_reconciled_at"), "size_matches_latest": size_matches,
            "all_confirmed_size": str(all_size), "confirmed_size": str(size), "confirmed_notional": str(cash),
            "vwap": str(cash / size) if size else None, "fee_complete": fees_complete,
            "fee_usdc": str(fees) if fees is not None else None, "errors": errors,
            "fills": selected, "post_cutoff_fill_count": len(accepted) - len(selected),
            "fill_state_counts": dict(Counter(f.get("status", "UNKNOWN") for f in fills))}


def resolution_evidence(trade, catalog, observations, *, end, naive_utc=False):
    """Validate exact token payout; no official score or text-only shortcut."""
    token, condition, label = str(trade.get("token_id") or ""), str(trade.get("condition_id") or ""), trade.get("outcome")
    proofs = []
    for row in observations:
        observed = maybe_time(row.get("observed_at"), naive_utc=naive_utc)
        if (row.get("condition_id") != condition or row.get("selected_token_id") != token
                or row.get("selected_outcome") != label or observed is None or observed >= end):
            continue
        body = row.get("evidence_json")
        if not isinstance(body, str) or hashlib.sha256(body.encode()).hexdigest() != row.get("evidence_sha256"):
            continue
        payload = json_value(body, {})
        tokens = payload.get("tokens", [])
        if payload.get("closed") is not True or len(tokens) != 2 or not all(isinstance(t, dict) for t in tokens):
            continue
        if len({t.get("token_id") for t in tokens}) != 2:
            continue
        try:
            values = [number(t.get("price")) for t in tokens]
            winners = [i for i, t in enumerate(tokens) if t.get("winner") is True]
            selected = [i for i, t in enumerate(tokens) if t.get("token_id") == token and t.get("outcome") == label]
            if (sorted(values) != [0, 1] or len(winners) != 1 or values[winners[0]] != 1
                    or len(selected) != 1 or values[selected[0]] != number(row.get("selected_payout"))
                    or row.get("winner_index") != winners[0]
                    or row.get("winner_token_id") != tokens[winners[0]].get("token_id")
                    or row.get("winner_outcome") != tokens[winners[0]].get("outcome")):
                continue
        except ValueError:
            continue
        proofs.append({"payout": str(values[selected[0]]), "observed_at": iso(observed),
                       "basis": "HASH_VERIFIED_EXACT_CLOB_TOKEN_PAYOUT", "evidence_sha256": row["evidence_sha256"]})
    observed = maybe_time(trade.get("resolution_observed_at"), naive_utc=naive_utc)
    if (isinstance(catalog, dict) and catalog.get("condition_id") == condition and catalog.get("closed") == 1
            and observed is not None and observed < end
            and str(trade.get("resolution_evidence") or "").startswith("gamma_closed_final_outcome_prices+execution_ledger_exact_confirmed_buy")):
        labels = json_value(catalog.get("outcomes_json"), [])
        tokens = json_value(catalog.get("token_ids_json"), [])
        prices = json_value(catalog.get("outcome_prices_json"), [])
        try:
            values = [number(v) for v in prices]
            final = (sorted(values) == [0, 1] or
                     (values == [Decimal(".5"), Decimal(".5")] and catalog.get("resolution_status") == "resolved"))
            selected = [i for i, (t, name) in enumerate(zip(tokens, labels)) if str(t) == token and name == label]
            if (len(tokens) == len(labels) == len(values) == 2 and len(set(tokens)) == 2 and len(set(labels)) == 2
                    and final and len(selected) == 1 and values[selected[0]] == number(trade.get("resolution_value"))):
                proofs.append({"payout": str(values[selected[0]]), "observed_at": iso(observed),
                               "basis": "EXACT_NORMALIZED_GAMMA_CATALOG_AND_TRADE_ATTESTATION",
                               "raw_http_response_available": False})
        except (ValueError, TypeError):
            pass
    if not proofs:
        return {"verified": False, "reason": "RESOLUTION_UNVERIFIED", "cash_redemption_proven": False}
    if len({number(p["payout"]) for p in proofs}) != 1:
        return {"verified": False, "reason": "CONFLICTING_RESOLUTION_PROOFS", "cash_redemption_proven": False}
    proof = min(proofs, key=lambda p: p["observed_at"])
    return {"verified": True, **proof, "cash_redemption_proven": False}


def position_economics(buy, sells, payout, *, start, end):
    """Pro-rate confirmed BUY cost to sold shares; keep residual value separate."""
    result = {"reason": None, "realized_sold_net_in_window": None, "settlement_value_net_in_window": None,
              "confirmed_economic_net_in_window": None, "residual_shares": None,
              "cash_redemption_proven": False, "carry_out": True}
    if buy["state"] == "PROVEN_ZERO_FILL":
        return {**result, "reason": "PROVEN_ZERO_FILL", "carry_out": False, "residual_shares": "0"}
    if not buy.get("complete_now") or not buy.get("fee_complete") or number(buy["confirmed_size"]) <= 0:
        return {**result, "reason": "BUY_COVERAGE_OR_FEE_UNCONFIRMED"}
    if any(s["state"] != "PROVEN_ZERO_FILL" and
           (not s.get("complete_now") or not s.get("fee_complete")) for s in sells):
        return {**result, "reason": "SELL_COVERAGE_OR_FEE_UNCONFIRMED"}
    size = number(buy["confirmed_size"])
    basis = (number(buy["confirmed_notional"]) + number(buy["fee_usdc"])) / size
    sold_fills = [f for s in sells for f in s.get("fills", [])]
    if sold_fills and min(utc(f["matched_at"]) for f in sold_fills) < max(utc(f["matched_at"]) for f in buy["fills"]):
        return {**result, "reason": "SELL_PRECEDES_COMPLETE_BUY_FILL"}
    sold = sum((number(f["size"]) for f in sold_fills), Decimal())
    residual = size - sold
    if residual < -EPS:
        return {**result, "reason": "CONFIRMED_SELL_EXCEEDS_BUY"}
    if residual < 0:
        return {**result, "reason": "NEGATIVE_RESIDUAL_REQUIRES_RECONCILIATION"}
    realized = sum((number(f["notional"]) - number(f["fee_usdc"]) - number(f["size"]) * basis
                    for f in sold_fills if start <= utc(f["matched_at"]) < end), Decimal())
    settlement = None
    if payout.get("verified") and start <= utc(payout["observed_at"]) < end:
        settlement = residual * (number(payout["payout"]) - basis)
    closed = residual == 0 or payout.get("verified", False)
    known = realized + (settlement or Decimal())
    return {**result, "reason": "CONFIRMED_SOLD_AND_RESOLUTION_VALUE" if settlement is not None else
            "CONFIRMED_SOLD_PORTION" if sold_fills else "OPEN_CONFIRMED_EXPOSURE",
            "realized_sold_net_in_window": str(realized),
            "settlement_value_net_in_window": str(settlement) if settlement is not None else None,
            "confirmed_economic_net_in_window": str(known) if closed or sold_fills or settlement is not None else None,
            "residual_shares": str(residual), "carry_out": not closed,
            "residual_cost_basis": str(residual * basis), "buy_unit_cost_including_fee": str(basis)}


def _rows(conn, table):
    return [dict(r) for r in conn.execute('SELECT * FROM "' + table + '"')]


def sport_for(trade, catalog, config):
    for source in (trade, catalog or {}, config):
        value = source.get("sport_family") or source.get("sport")
        if value in SPORTS:
            return value
    tags = json_value((catalog or {}).get("tags_json"), []) or json_value(trade.get("market_tags_json"), [])
    names = {str(t.get("slug", "")).lower() if isinstance(t, dict) else str(t).lower() for t in tags}
    for sport, aliases in (("mlb", {"mlb", "baseball"}), ("nba", {"nba"}), ("nfl", {"nfl"}),
                           ("nhl", {"nhl"}), ("soccer", {"soccer", "epl", "laliga", "serie-a", "mls", "bundesliga", "ligue-1"})):
        if names & aliases:
            return sport
    return "unknown"


def _cohort(sub, runs, configs):
    run = runs.get((sub or {}).get("run_id"), {})
    cfg = configs.get(run.get("config_hash"), {})
    trading = cfg.get("trading", cfg)
    return {"run_id": run.get("run_id"), "run_status": run.get("status"), "mode": run.get("mode"),
            "runtime_job": run.get("job_name"), "config_hash": run.get("config_hash"),
            "source_digest": trading.get("strategy_source_digest") or cfg.get("strategy_source_digest"),
            "git_commit": run.get("git_commit")}


def read_source(source, *, start, end):
    path = Path(source["db_path"]).resolve()
    if source.get("mode") != "live" or source.get("pinned") is not True or "pinned" not in path.parts:
        raise ValueError("explicit live standalone pin required")
    manifest = json.loads(Path(source.get("manifest") or path.with_name("manifest.json")).read_text())
    if (manifest.get("pinned_path") != str(path) or manifest.get("source_key") != source.get("source_key")
            or manifest.get("sha256") != source["sha256"] or manifest.get("quick_check") != ["ok"]):
        raise ValueError("pin manifest identity mismatch")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("pin must have no SQLite sidecars")
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != source["sha256"]:
            raise ValueError("pin checksum mismatch")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    base = {"source_key": source["source_key"], "jenkins_job": source["jenkins_job"], "strategy": source["strategy"],
            "runtime_job": source["runtime_job"], "sha256": source["sha256"], "pin": str(path),
            "snapshot_source_completed_at": source.get("source_completed_at"), "positions": [], "orders_without_trade": [], "events": [], "issues": [], "unclassified_period_requests": []}
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        needed = {"trades", "run_audits", "strategy_configs", "order_submissions", "order_fills"}
        if not needed <= tables:
            return {**base, "status": "UNSUPPORTED_SCHEMA", "missing_tables": sorted(needed - tables)}
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("pin quick_check failed")
        runs = {r["run_id"]: r for r in _rows(conn, "run_audits")}
        configs = {r["config_hash"]: json_value(r["config_json"], {}) for r in _rows(conn, "strategy_configs")}
        if not runs:
            return {**base, "status": "NO_RUN_EVIDENCE"}
        latest = max(runs.values(), key=lambda r: r["started_at"])
        if latest.get("mode") != "live" or latest.get("job_name") != source["runtime_job"] or latest.get("strategy_name") != source["strategy"]:
            return {**base, "status": "SOURCE_RUNTIME_MODE_MISMATCH", "latest_mode": latest.get("mode"), "latest_runtime": latest.get("job_name")}
        current = configs.get(latest["config_hash"], {})
        current = current.get("trading", current)
        catalogs = {r["condition_id"]: r for r in _rows(conn, "market_catalog")} if "market_catalog" in tables else {}
        trades = _rows(conn, "trades")
        submissions = [s for s in _rows(conn, "order_submissions") if s.get("simulation") == 0]
        fills = defaultdict(list)
        for fill in _rows(conn, "order_fills"):
            fills[fill["submission_id"]].append(fill)
        resolution_rows = _rows(conn, "resolution_observations") if "resolution_observations" in tables else []
        by_order, by_token, trade_tokens = defaultdict(list), defaultdict(list), defaultdict(list)
        for sub in submissions:
            by_order[sub.get("order_id")].append(sub)
            by_token[sub.get("token_id")].append(sub)
        for trade in trades:
            trade_tokens[trade.get("token_id")].append(trade)
        naive = source.get("timestamp_contract") == "repository_sqlite_utc"
        def evidence(sub):
            value = order_evidence(sub, fills.get((sub or {}).get("submission_id"), []), end=end,
                                   maker_zero_fee_contract=source.get("maker_zero_fee_contract") is True, naive_utc=naive)
            value["cohort"] = _cohort(sub, runs, configs)
            if value.get("cohort", {}).get("mode") not in (None, "live"):
                value.update(complete_now=False, state="CONFIRMED_INCOMPLETE", reason="ORDER_RUN_MODE_MISMATCH")
            return value
        used, events = set(), {}
        unclassified = Counter()
        for trade in trades:
            catalog = catalogs.get(trade.get("condition_id"), {})
            sport = sport_for(trade, catalog, current)
            if sport not in SPORTS:
                requested = maybe_time(trade.get('buy_timestamp'), naive_utc=naive)
                if requested and start <= requested < end:
                    unclassified['period_requests_outside_supported_families_or_unclassified'] += 1
                    base['unclassified_period_requests'].append({'question':trade.get('question'), 'outcome':trade.get('outcome'),
                        'requested_at_utc':iso(requested), 'requested_at_kst':kst(requested), 'trade_status_not_fill_proof':trade.get('status'),
                        'reason':'SUPPORTED_SPORT_FAMILY_NOT_PROVEN; excludes non-target sports and missing metadata; no zero-PnL inference'})
                if requested and requested < end and trade.get('status') in ACTIVE_TRADES:
                    unclassified['active_lifecycle_rows_outside_supported_families_or_unclassified'] += 1
                continue
            requested_at = maybe_time(trade.get("buy_timestamp"), naive_utc=naive)
            if requested_at is None or requested_at >= end:
                continue
            eid = str(trade.get("event_id") or catalog.get("event_id") or "condition:" + str(trade.get("condition_id")))
            title = catalog.get("event_title") or trade.get("event_slug") or trade.get("question") or eid
            buy_candidates = [s for s in by_order.get(trade.get("buy_order_id"), []) if s.get("side") == "BUY" and s.get("token_id") == trade.get("token_id")]
            buy_sub = buy_candidates[0] if len(buy_candidates) == 1 else None
            buy = evidence(buy_sub)
            if buy_sub:
                used.add(buy_sub["submission_id"])
            sell_subs = [s for s in by_token[trade.get("token_id")] if s.get("side") == "SELL"
                         and (when := maybe_time(s.get("submitted_at"), naive_utc=naive)) is not None
                         and requested_at <= when < end]
            ambiguous = len(trade_tokens[trade.get("token_id")]) > 1
            if ambiguous:
                sell_subs = [s for s in sell_subs if s.get("order_id") == trade.get("sell_order_id")]
            sells = [evidence(s) for s in sell_subs]
            used.update(s["submission_id"] for s in sell_subs)
            payout = resolution_evidence(trade, catalog, resolution_rows, end=end, naive_utc=naive)
            economic = position_economics(buy, sells, payout, start=start, end=end)
            if ambiguous:
                economic.update(reason="MULTIPLE_BUYS_FOR_TOKEN_REQUIRE_EXACT_SELL_ALLOCATION",
                                realized_sold_net_in_window=None, settlement_value_net_in_window=None,
                                confirmed_economic_net_in_window=None, carry_out=True)
            entry_times = [utc(f["matched_at"]) for f in buy.get("fills", [])]
            entry = min(entry_times) if entry_times else requested_at
            in_entry = bool(entry_times) and start <= entry < end
            in_request = start <= requested_at < end
            in_exit = any(start <= utc(f["matched_at"]) < end for s in sells for f in s.get("fills", []))
            in_payout = payout.get("verified") and start <= utc(payout["observed_at"]) < end
            carry_open = entry < start and (economic.get("carry_out") or trade.get("status") in ACTIVE_TRADES)
            if not (in_entry or in_request or in_exit or in_payout or carry_open):
                continue
            events[eid] = {"event_id": eid, "sport": sport, "title": title, "league": trade.get("league_code") or catalog.get("league_code"), "observed": True}
            base["positions"].append({"trade_ref": ref([source["source_key"], trade["id"]]), "event_id": eid,
                "sport": sport, "title": title, "question": trade.get("question"), "outcome": trade.get("outcome"),
                "token_ref": ref(trade.get("token_id")), "entry_utc": iso(entry), "entry_kst": kst(entry),
                "entry_time_basis": "CONFIRMED_MATCH" if entry_times else "UNCONFIRMED_REQUEST",
                "entry_in_window": in_entry, "request_in_window": in_request,
                "carry_in": entry < start, "exit_in_window": in_exit,
                "trade_status": trade.get("status"), "entry_reason": trade.get("entry_reason"), "exit_reason": trade.get("exit_reason"),
                "entry_policy": {k: trade.get(k) for k in ("take_profit_price_at_buy", "take_profit_delta_at_buy", "stop_price_at_entry", "stop_loss_delta_at_buy", "entry_prob_min_at_buy", "entry_prob_max_at_buy", "source_elapsed_minutes_at_buy", "late_exit_minute_at_buy")},
                "buy": buy, "sells": sells, "resolution": payout, "economics": economic,
                "trade_pnl_column_used": False})
        token_catalogs = defaultdict(list)
        for catalog in catalogs.values():
            for token in json_value(catalog.get("token_ids_json"), []):
                token_catalogs[str(token)].append(catalog)
        for sub in submissions:
            if sub["submission_id"] in used:
                continue
            submitted = maybe_time(sub.get("submitted_at"), naive_utc=naive)
            if submitted is None or not start <= submitted < end:
                continue
            cs = token_catalogs.get(str(sub.get("token_id")), [])
            catalog = cs[0] if len(cs) == 1 else {}
            sport = sport_for({}, catalog, current)
            if sport not in SPORTS:
                continue
            eid = str(catalog.get("event_id") or "unknown-event:" + ref(sub["submission_id"]))
            events.setdefault(eid, {"event_id": eid, "sport": sport, "title": catalog.get("event_title") or catalog.get("question") or eid, "league": catalog.get("league_code"), "observed": True})
            base["orders_without_trade"].append({"event_id": eid, "sport": sport, "reason": "ORDER_WITHOUT_UNAMBIGUOUS_TRADE_LINK", "order": evidence(sub)})
        if "market_snapshots" in tables:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(market_snapshots)")}
            if {"condition_id", "timestamp"} <= columns:
                coarse = (start.date().isoformat(), (end.date() + timedelta(days=1)).isoformat())
                counts, first, last = Counter(), {}, {}
                for row in conn.execute("SELECT condition_id,timestamp FROM market_snapshots WHERE timestamp>=? AND timestamp<?", coarse):
                    when = maybe_time(row["timestamp"], naive_utc=naive)
                    if when is None or not start <= when < end:
                        continue
                    catalog = catalogs.get(row["condition_id"], {})
                    sport = sport_for({}, catalog, current)
                    if sport not in SPORTS or not catalog.get("event_id"):
                        continue
                    eid = str(catalog["event_id"])
                    events.setdefault(eid, {"event_id": eid, "sport": sport, "title": catalog.get("event_title") or catalog.get("question") or eid, "league": catalog.get("league_code"), "observed": True})
                    counts[eid] += 1
                    first[eid] = min(first.get(eid, when), when)
                    last[eid] = max(last.get(eid, when), when)
                for eid, count in counts.items():
                    events[eid].update(snapshot_rows=count, first_snapshot=iso(first[eid]), last_snapshot=iso(last[eid]))
        if "entry_episodes" in tables:
            for row in _rows(conn, "entry_episodes"):
                eid = str(row.get("event_id") or "")
                if eid in events:
                    events[eid].setdefault("entry_episode_states", []).append({k: row[k] for k in ("status", "reason", "last_reason", "state") if k in row})
        base["events"] = list(events.values())
        selected_runs = [r for r in runs.values() if (when := maybe_time(r.get("started_at"), naive_utc=naive)) and start <= when < end]
        guards = Counter()
        for run in selected_runs:
            guard = json_value(run.get("cycle_stats_json"), {}).get("entry_guard", {})
            if guard.get("blocked"):
                guards.update(guard.get("blocking_reasons") or ["ENTRY_GUARD_BLOCKED"])
        base.update(status="ANALYZED", sport_profile=sport_for({}, {}, current),
                    classification_coverage={"all_trade_rows":len(trades), **dict(unclassified)},
                    activity_scope="WINDOW_ACTIVITY_OR_CARRY_IN" if (selected_runs or base['positions'] or base['orders_without_trade'] or events) else "HISTORICAL_INACTIVE_IN_WINDOW",
                    current_config_hash=latest["config_hash"], current_source_digest=current.get("strategy_source_digest"),
                    run_counts=dict(Counter(r.get("status") for r in selected_runs)), entry_guard_counts=dict(guards),
                    latest_run={k: latest.get(k) for k in ("started_at", "finished_at", "status", "job_name", "mode")},
                    cohort_basis="entry order run/config; SELL may belong to a later code cohort",
                    reconciliation_basis="CONFIRMED and reconciled as of the verified snapshot; period uses match time",
                    timestamp_contract=source.get("timestamp_contract"))
        return base
    finally:
        conn.close()


def build_report(inputs, *, start, end, official=None):
    start, end = utc(start), utc(end)
    if start >= end:
        raise ValueError("report start must precede exclusive end")
    if inputs.get("schema") != "sports-trade-report-inputs-v1":
        raise ValueError("unsupported report inputs")
    sources_input = inputs.get("sources", [])
    if (len({s["source_key"] for s in sources_input}) != len(sources_input)
            or len({str(Path(s["db_path"]).resolve()) for s in sources_input}) != len(sources_input)):
        raise ValueError("duplicate source pin must not be counted twice")
    for key, value in (("review_start", start), ("review_end_exclusive", end)):
        if inputs.get(key) and utc(inputs[key]) != value:
            raise ValueError("report period differs from prepared inputs")
    sources = [read_source(s, start=start, end=end) for s in inputs["sources"]]
    games = {}
    for source in sources:
        for game in source["events"]:
            key = (game["sport"], game["event_id"])
            games.setdefault(key, {**game, "sources": []})["sources"].append(source["source_key"])
    for game in (official or {}).get("games", []):
        for event_id in game.get("venue_event_ids", []):
            key = (game["sport"], str(event_id))
            games.setdefault(key, {"sport": game["sport"], "event_id": str(event_id), "title": game["title"], "sources": []})["official"] = game
        if not game.get("venue_event_ids"):
            key = (game["sport"], "official:" + str(game["official_id"]))
            games.setdefault(key, {"sport": game["sport"], "event_id": key[1], "title": game["title"], "sources": [], "official": game})
    totals = []
    active_game_keys = {(p['sport'],p['event_id']) for source in sources for p in source['positions']
                        if p['entry_in_window'] or p['request_in_window'] or p['exit_in_window']}
    active_game_keys.update((p['sport'],p['event_id']) for source in sources for p in source['orders_without_trade'])
    for key, game in games.items():
        game['report_scope'] = ('OFFICIAL_PERIOD_MATCH' if game.get('official') else 'PERIOD_TRADE_ACTIVITY'
                                if key in active_game_keys else 'CARRY_IN_OR_DISCOVERY_ONLY')
    for source in sources:
        positions = source["positions"]
        known = [p for p in positions if p["economics"]["confirmed_economic_net_in_window"] is not None]
        totals.append({"source_key": source["source_key"], "strategy": source["strategy"], "jenkins_job": source["jenkins_job"],
                       "runtime_job": source["runtime_job"], "positions": len(positions), "known_economic_positions": len(known),
                       "unconfirmed_positions": len(positions) - len(known),
                       "confirmed_sold_net": str(sum((number(p["economics"]["realized_sold_net_in_window"]) for p in known), Decimal())),
                       "settlement_value_net": str(sum((number(p["economics"]["settlement_value_net_in_window"]) for p in known if p["economics"]["settlement_value_net_in_window"] is not None), Decimal())),
                       "known_economic_subtotal": str(sum((number(p["economics"]["confirmed_economic_net_in_window"]) for p in known), Decimal())) if source['status']=='ANALYZED' else None,
                       "source_status": source['status'],
                       "unclassified_period_requests": len(source.get('unclassified_period_requests', [])),
                       "orphans": len(source["orders_without_trade"]), "cash_redemption_proven": False})
    return {"schema": SCHEMA, "window": {"start_utc": iso(start), "end_exclusive_utc": iso(end), "start_kst": kst(start), "end_exclusive_kst": kst(end)},
            "created_at": iso(datetime.now(timezone.utc)), "sources": sources,
            "games": sorted(games.values(), key=lambda g: (SPORTS.index(g["sport"]) if g["sport"] in SPORTS else 99, g["title"])),
            "summary_by_source": totals, "preparation_gaps": inputs.get("gaps", []),
            "discovery_jobs": inputs.get('discovery_jobs', []),
            "case_notes": inputs.get('case_notes', []),
            "no_orders_or_remote_changes": True, "official_result_coverage": (official or {}).get("coverage", {}),
            "performance_basis": "confirmed SELL realized net plus separately identified settlement claim value; not cash redemption"}


def _money(value):
    return "미확정" if value is None else f"{number(value):+.6f}"


def _text(value):
    return html.escape(str(value if value is not None else "—"), quote=False).replace("|", "\\|").replace("\n", " ")


def render_markdown(report):
    w = report["window"]
    lines = ["# 스포츠 경기별 실제 체결 보고", "", f"UTC [{w['start_utc']}, {w['end_exclusive_utc']})", f"KST [{w['start_kst']}, {w['end_exclusive_kst']})", "",
             "CONFIRMED fill을 고정 사본 기준으로 대사했다. 정산 지급권 가치는 실제 현금 상환과 분리하며, 미확정 금액은 0으로 채우지 않는다.", ""]
    for sport in SPORTS:
        games = [g for g in report["games"] if g["sport"] == sport and g.get('report_scope') != 'CARRY_IN_OR_DISCOVERY_ONLY']
        lines.extend([f"## {SPORT_LABELS[sport]} — {len(games)}개 경기 식별자", ""])
        if not games:
            lines.extend(["선택한 live 원장/공식 결과에서 확인된 경기 없음. 종목 미운영·자료 미수집과 수익 0은 다르다.", ""])
            continue
        for game in games:
            lines.extend([f"### {_text(game['title'])}", ""])
            official = game.get("official")
            if official:
                lines.append(f"공식: {_text(official.get('home'))} {_text(official.get('home_score'))}–{_text(official.get('away_score'))} {_text(official.get('away'))} · {_text(official.get('status'))} · [{_text(official.get('league') or '공식 경기 자료')}]({official.get('source_url','')})")
                scheduled = official.get('scheduled_at')
                lines.append(f"경기 예정 시작: {_text(scheduled)} UTC / {_text(kst(scheduled) if scheduled else None)} KST · 공식 경기 날짜 {_text(official.get('game_date'))}")
            else:
                lines.append("공식 결과: 미확인 — 원장 정산이나 사용자 메모로 대신하지 않음.")
            for note in report.get('case_notes', []):
                if note.get('sport') == sport and str(note.get('event_id')) == game['event_id']:
                    lines.extend(['', _text(note['text']), '근거: '+_text(note.get('evidence_file'))])
            lines.extend(["", "| 전략 · Jenkins · runtime | 선택 결과 | BUY 확정 시각 UTC / KST · VWAP · 수량 · 원금 | SELL/종료 | 매도 순손익 | 정산 가치 손익 | 상태·잔여·미체결 이유 |", "|---|---|---|---|---:|---:|---|"])
            for source in report["sources"]:
                if source.get('activity_scope') == 'HISTORICAL_INACTIVE_IN_WINDOW':
                    continue
                if source.get("sport_profile") not in (sport, "unknown") and not any(p['sport']==sport for p in source['positions']):
                    continue
                label = f"{source['strategy']} · {source['jenkins_job']} · {source['runtime_job']}"
                positions = [p for p in source["positions"] if p["event_id"] == game["event_id"] and p["sport"] == sport]
                if not positions:
                    observed = next((g for g in source['events'] if g['event_id']==game['event_id']), None)
                    reason = "NO_TRADE_RECORDED" if observed else "NOT_DISCOVERED_IN_SELECTED_SOURCE"
                    if observed and observed.get('entry_episode_states'):
                        reason += ' · episode=' + json.dumps(observed['entry_episode_states'], ensure_ascii=False)
                    if source.get('entry_guard_counts'):
                        reason += " · source 구간 guard=" + ",".join(source['entry_guard_counts'])
                    lines.append(f"| {_text(label)} | — | — | — | 미확정/해당 없음 | — | {_text(reason)} |")
                for p in positions:
                    b, e = p['buy'], p['economics']
                    buy = f"{p['entry_utc']} / {p['entry_kst']} · {b.get('vwap','—')} · {b['confirmed_size']}주 · {b['confirmed_notional']} USDC"
                    policy = ', '.join(f'{k}={v}' for k,v in p.get('entry_policy', {}).items() if v is not None)
                    buy += ' · 진입 사유: ' + (p.get('entry_reason') or '기록 없음') + ' · 진입 당시 정책: ' + (policy or '원장에 기록 없음')
                    sales = [f"{f['matched_at']} / {f['matched_kst']} · {f['price']}×{f['size']}" for s in p['sells'] for f in s.get('fills',[])]
                    if p['resolution'].get('verified'):
                        sales.append(f"정산관측 {p['resolution']['observed_at']} · 지급 {p['resolution']['payout']}/주")
                    sales.append('원장 청산 사유: ' + (p.get('exit_reason') or '기록 없음/미청산'))
                    state = f"{e['reason']} · residual={e['residual_shares']} · {'carry-in' if p['carry_in'] else '기간내진입' if p['entry_in_window'] else '기간내요청/체결미확인'} · {'carry-out' if e['carry_out'] else '종결/정산가치확인'}"
                    lines.append("| " + " | ".join(map(_text, [label, f"{p['question']} · {p['outcome']}", buy, '; '.join(sales), _money(e['realized_sold_net_in_window']), _money(e['settlement_value_net_in_window']), state])) + " |")
                for orphan in source['orders_without_trade']:
                    if orphan['event_id']==game['event_id']:
                        lines.append(f"| {_text(label)} | 주문 연결 미확정 | {_text(orphan['order']['state'])} · 확정 {orphan['order']['confirmed_size']}주 | — | 미확정 | — | {_text(orphan['reason'])} · {_text(orphan['order'].get('response_status'))} · {_text(orphan['order'].get('error_type'))} |")
            lines.append("")
    lines.extend(['## 과거 진입의 잔여 노출·추가 관측 부록', '', '최근 경기 결과와 별도로 과거 진입의 미정산 잔여 수량을 보존한다. 요청 상태 COMPLETED만으로 잔여 수량을 0으로 만들지 않았다. 시각·호가·원장 상세는 같은 폴더 report.json에도 있다.', '', '| 종목 · 경기 | Jenkins · runtime | 선택 결과 · 원 진입 UTC / KST | BUY 확정 수량 | 잔여 수량 | 상태 / 정산 근거 |', '|---|---|---|---:|---:|---|'])
    auxiliary_ids={(g['sport'],g['event_id']) for g in report['games'] if g.get('report_scope')=='CARRY_IN_OR_DISCOVERY_ONLY'}
    for source in report['sources']:
        for p in source['positions']:
            if (p['sport'],p['event_id']) not in auxiliary_ids:
                continue
            e=p['economics']
            lines.append('| '+' | '.join(map(_text,[p['sport']+' · '+p['title'],source['jenkins_job']+' · '+source['runtime_job'],f"{p['question']} · {p['outcome']} · {p['entry_utc']} / {p['entry_kst']}",p['buy']['confirmed_size'],e['residual_shares'],e['reason']+' · '+p['resolution'].get('reason','')]))+' |')
    lines.extend(['', '### 거래 없는 추가 발견 원장', '', '| 종목 · event | 관측 제목 | 구분 |', '|---|---|---|'])
    position_ids={(p['sport'],p['event_id']) for source in report['sources'] for p in source['positions']}
    for g in report['games']:
        if (g['sport'],g['event_id']) in auxiliary_ids-position_ids:
            lines.append('| '+' | '.join(map(_text,[g['sport']+' · '+g['event_id'],g['title'],'발견 증거만 있음 · 공식 기간 경기/체결로 세지 않음']))+' |')
    lines.extend(['', '### 혼합 전략의 지원 종목 밖·종목 미확인 요청', '', '아래 요청은 5개 지원 종목이라는 catalog 근거가 부족하거나 다른 종목이다. 요청 상태를 체결로 간주하지 않았으며 주력 경기 손익 합계에 포함하지 않았다.', '', '| Jenkins | 요청 UTC / KST | 시장 · 선택 | 요청 원장 상태(체결 증거 아님) |', '|---|---|---|---|'])
    for source in report['sources']:
        for p in source.get('unclassified_period_requests', []):
            lines.append('| '+' | '.join(map(_text,[source['jenkins_job'],p['requested_at_utc']+' / '+p['requested_at_kst'],str(p['question'])+' · '+str(p['outcome']),p['trade_status_not_fill_proof']]))+' |')
    lines.extend(["## 전략·runtime별 확인 금액", "", "| Jenkins · runtime | 확인 포지션 / 미확정 | 매도 순손익 | 정산 지급권 손익 | 확인 부분 합계 |", "|---|---:|---:|---:|---:|"])
    for row in report['summary_by_source']:
        lines.append(f"| {_text(row['jenkins_job']+' · '+row['runtime_job'])} | {row['known_economic_positions']} / {row['unconfirmed_positions']} · 지원종목 밖/미확인 요청 {row.get('unclassified_period_requests',0)} | {_money(row['confirmed_sold_net'])} | {_money(row['settlement_value_net'])} | {_money(row['known_economic_subtotal'])} |")
    lines.extend(["", "같은 경기의 여러 전략·계좌·결과를 독립 경기 수로 더하지 않는다. 이 합계는 미확정 포지션/수수료/미연결 주문을 포함한 전체 계좌 순이익이 아니다. Cash redemption, 입출금·보상·리베이트·가스는 별도 증거가 필요하다.", "", "## 사용한 사본", ""])
    for source in report['sources']:
        lines.append(f"- {source['jenkins_job']} / {source['runtime_job']} · {source['status']} · SHA-256 `{source['sha256']}` · cutoff {source.get('snapshot_source_completed_at')} · `{source['pin']}`")
        lines.append(f"  - run 상태 {_text(source.get('run_counts'))} · 신규진입 차단 {_text(source.get('entry_guard_counts'))} · 종목 분류 범위 {_text(source.get('classification_coverage'))}")
    lines.extend(['', '## 현재 job 발견·제외 범위', '', '| Jenkins | 전략 | 현재 실행 구분 | 이번 원장 후보 |', '|---|---|---|---|'])
    for job in report.get('discovery_jobs', []):
        lines.append('| '+' | '.join(map(_text,[job['job'],job.get('strategy'),job.get('reason'),job.get('selected')]))+' |')
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--official-results", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_report(json.loads(args.inputs.read_text()), start=args.start, end=args.end,
                          official=json.loads(args.official_results.read_text()) if args.official_results else None)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (args.output / "report.md").write_text(render_markdown(report))
    print(json.dumps({"sources": len(report['sources']), "games": len(report['games']), "report": str(args.output / 'report.md')}))


if __name__ == "__main__":
    main()
