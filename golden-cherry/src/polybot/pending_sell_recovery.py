"""Recover a fully sold own position without inventing an old signed amount.

This narrow legacy path requires complete authenticated TAKER fills and an
exact cancellation/terminal acknowledgement. It never creates a new order,
re-signs historical requests, or uses wallet absence as fill evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3
from typing import Any, Mapping

from polybot_observability import normalize_clob_response, normalize_clob_response_list

PROOF = "CHERRY_EXACT_CONFIRMED_SELL_TERMINAL_ACK_V1"


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a quantity")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite quantity")
    return number


def _status(value: Any) -> str:
    return str(value or "").upper().removeprefix("TRADE_STATUS_")


def exact_taker_fills(payload: Any, order_id: str, token_id: str) -> list[dict]:
    """Select exact own-order fills; never infer NO prices or maker allocations."""
    trades = normalize_clob_response_list(payload, response_type="trade")
    selected: dict[tuple[str, int], dict] = {}
    for trade in trades:
        if str(trade.get("taker_order_id") or "") != order_id:
            if any(str(m.get("order_id") or "") == order_id for m in trade.get("maker_orders", [])):
                raise ValueError("legacy maker recovery requires separate proof")
            continue
        if (
            str(trade.get("asset_id") or "") != token_id
            or str(trade.get("side") or "").upper() != "SELL"
            or _status(trade.get("status")) != "CONFIRMED"
            or str(trade.get("trader_side") or "TAKER").upper() != "TAKER"
        ):
            raise ValueError("exact SELL fill identity/status mismatch")
        trade_id = str(trade.get("id") or "")
        bucket = trade.get("bucket_index", 0)
        if not trade_id or isinstance(bucket, bool) or int(bucket) != _number(bucket) or int(bucket) < 0:
            raise ValueError("invalid trade/bucket identity")
        size, price = _number(trade.get("size")), _number(trade.get("price"))
        if size <= 0 or not 0 < price <= 1:
            raise ValueError("invalid confirmed fill amount")
        # All currently stuck Cherry TAKER fills explicitly attest rate=0.
        # A missing or nonzero rate is not silently converted to a known fee.
        if trade.get("fee_rate_bps") is None or _number(trade["fee_rate_bps"]) != 0:
            raise ValueError("this recovery requires an explicit zero TAKER fee")
        row = {"trade_id": trade_id, "bucket_index": int(bucket), "size": size,
               "price": price, "status": "CONFIRMED", "side": "SELL", "fee_rate_bps": 0}
        key = (trade_id, int(bucket))
        if key in selected and selected[key] != row:
            raise ValueError("conflicting duplicate fill")
        selected[key] = row
    if not selected:
        raise ValueError("no exact confirmed SELL fills")
    return [selected[key] for key in sorted(selected)]


def terminal_ack(payload: Any, order_id: str) -> tuple[str, dict]:
    response = normalize_clob_response(payload, response_type="cancellation")
    canceled, rejected = response.get("canceled"), response.get("not_canceled")
    if not isinstance(canceled, (list, tuple)) or not isinstance(rejected, Mapping):
        raise ValueError("malformed cancellation acknowledgement")
    if list(canceled) == [order_id] and not rejected:
        return "CANCELED", {"kind": "exact_cancel_ack"}
    if canceled or set(rejected) != {order_id}:
        raise ValueError("cancellation does not identify exactly the own order")
    reason = str(rejected[order_id]).strip().lower()
    if "fully filled" in reason or "already been filled" in reason or "already filled" in reason:
        return "MATCHED", {"kind": "exact_already_filled_ack", "reason": reason}
    if "not found" in reason or "already canceled" in reason or "already cancelled" in reason:
        # Derived terminal state, not a claim about the historical exchange
        # status. Proven matched quantity is retained, never set to zero.
        return "CANCELED", {"kind": "exact_terminal_absence_ack", "reason": reason}
    raise ValueError("cancellation does not prove terminal state")


def _assert_absent(client, order_id: str) -> None:
    from py_clob_client_v2 import OpenOrderParams

    current = normalize_clob_response_list(
        client.get_open_orders(OpenOrderParams(id=order_id), only_first_page=False),
        response_type="order",
    )
    legacy = normalize_clob_response_list(client.get_pre_migration_orders(), response_type="order")
    if any(str(row.get("id") or "") == order_id for row in [*current, *legacy]):
        raise ValueError("order is still present in an authoritative catalog")


def recover_fully_sold_legacy_position(repo, wrapper, trade) -> bool:
    """Only close the order evidence gap for a fully confirmed own pending SELL."""
    ledger = getattr(wrapper, "execution_ledger", None)
    if ledger is None or wrapper.simulation_mode:
        return False
    db_path = repo.session.get_bind().url.database
    if not db_path:
        return False
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT s.* FROM order_submissions s JOIN trades t ON t.sell_order_id=s.order_id "
            "WHERE t.id=? AND t.status='PENDING_SELL' AND s.side='SELL' "
            "AND s.simulation=0 AND s.strategy_name='golden-cherry'",
            (trade.id,),
        ).fetchall()
        if len(rows) != 1:
            return False
        submission = dict(rows[0])
    if (
        submission["token_id"] != trade.token_id
        or submission["latest_order_status"] is not None
        or submission["making_amount"] is not None
        or submission["taking_amount"] is not None
        or submission["reconciliation_proof"] != "AUTHENTICATED_TOKEN_TRADE_CATALOG_EXACT_IDS"
    ):
        return False
    created = datetime.fromisoformat(submission["submitted_at"].replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise ValueError("submission timestamp lacks timezone")
    if (datetime.now(timezone.utc) - created).total_seconds() < 1800:
        return False
    buy = repo.get_exact_buy_fill_evidence(trade.buy_order_id, trade.token_id)
    if not (buy.has_reconciled_matched_fill and buy.fee_complete and buy.confirmed_size):
        return False
    order_id, token_id = submission["order_id"], submission["token_id"]
    from py_clob_client_v2 import TradeParams

    def fresh_fills():
        raw = wrapper.client.get_trades(TradeParams(asset_id=token_id), only_first_page=False)
        return exact_taker_fills(raw, order_id, token_id)

    _assert_absent(wrapper.client, order_id)
    before = fresh_fills()
    known_size = _number(trade.buy_shares)
    confirmed = sum(row["size"] for row in before)
    if not math.isclose(confirmed, known_size, abs_tol=1e-6, rel_tol=0) or confirmed > buy.confirmed_size + 1e-6:
        return False
    state, acknowledgement = terminal_ack(wrapper.client.cancel_orders([order_id]), order_id)
    _assert_absent(wrapper.client, order_id)
    after = fresh_fills()
    if before != after:
        raise ValueError("fill catalog changed across terminal acknowledgement")
    expected_ids = sorted({row["trade_id"] for row in after})
    if sorted(json.loads(submission["associated_trade_ids_json"] or "[]")) != expected_ids:
        raise ValueError("associated trade set is incomplete")
    observed = datetime.now(timezone.utc).isoformat()
    proof = {"proof": PROOF, "observed_at": observed, "order_id": order_id,
             "token_id": token_id, "terminal_state": state, "acknowledgement": acknowledgement,
             "current_and_legacy_catalog_absent": True, "complete_token_catalog": True,
             "confirmed_fills": after, "original_signed_amount_reconstructed": False}
    proof_json = json.dumps(proof, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(proof_json.encode()).hexdigest()
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT status,sell_order_id,buy_shares FROM trades WHERE id=?", (trade.id,)).fetchone()
        if not current or current[0] != "PENDING_SELL" or current[1] != order_id or not math.isclose(current[2], known_size, abs_tol=1e-6, rel_tol=0):
            raise ValueError("managed position changed during recovery")
        persisted = connection.execute(
            "SELECT trade_id,bucket_index,status,side,size,price,fee_rate_bps,domain_error "
            "FROM order_fills WHERE submission_id=?", (submission["submission_id"],),
        ).fetchall()
        normalized = [{"trade_id": r[0], "bucket_index": r[1], "status": r[2], "side": r[3],
                       "size": r[4], "price": r[5], "fee_rate_bps": r[6]} for r in persisted]
        if any(r[7] is not None for r in persisted) or sorted(normalized, key=lambda r:(r['trade_id'],r['bucket_index'])) != after:
            raise ValueError("persisted fills differ from fresh authenticated fills")
        connection.execute(
            "INSERT OR IGNORE INTO order_status_events "
            "(submission_id,observed_at,status,original_size,size_matched,price,associated_trade_ids_json,fingerprint,domain_error) "
            "VALUES (?,?,?,NULL,?,?,?,?,NULL)",
            (submission["submission_id"], observed, state, confirmed, submission["requested_price"], json.dumps(expected_ids), fingerprint),
        )
        updated = connection.execute(
            "UPDATE order_submissions SET latest_order_status=?,latest_size_matched=?,last_reconciled_at=?,"
            "latest_status_domain_error=NULL,reconciliation_error=NULL,quantity_scale=1,needs_reconciliation=1,"
            "reconciliation_proof=?,outcome_resolution=?,outcome_resolved_at=?,outcome_resolution_reason=? "
            "WHERE submission_id=? AND order_id=? AND latest_order_status IS NULL AND needs_reconciliation=1",
            (state, confirmed, observed, PROOF, PROOF, observed, proof_json, submission["submission_id"], order_id),
        )
        if updated.rowcount != 1:
            raise ValueError("submission changed during recovery")
    completed = ledger.finish_reconciliation(submission["submission_id"])
    repo.session.expire_all()
    return completed
