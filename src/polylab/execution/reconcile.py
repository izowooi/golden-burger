"""Reconcile live orders to CONFIRMED fills, and settle positions from exact resolutions.

Order lifecycle (strategy DB `orders.status`):
  intent -> posted|matched -> confirmed (all trades CONFIRMED, fees known)
                            -> cancelled (proven zero fill; BUY position -> unfilled, SELL -> open)
                            -> failed (explicit venue rejection at POST)
  intent|unknown            -> stays blocking its token×side until evidence; 180 min -> position quarantined
Only CONFIRMED trades count; MATCHED/MINED/RETRYING are not terminal. A fill whose fee cannot
be computed (unknown schedule) keeps the order open — never a silent zero.
"""

from __future__ import annotations

from typing import Callable

from polylab.execution.fees import FeeSchedule, fee_usdc
from polylab.execution.ledger import StrategyLedger
from polylab.marketview import MarketView

FOK_ZERO_FILL_DELAY_S = 120
QUARANTINE_AFTER_S = 180 * 60
TERMINAL_ORDER = {"MATCHED", "CANCELED", "CANCELLED", "CANCELED_MARKET_RESOLVED", "INVALID", "UNMATCHED"}


ORDER_FIELDS = ("id", "status", "size_matched", "original_size", "price", "side", "created_at")
TRADE_FIELDS = ("id", "status", "size", "price", "match_time", "trader_side", "transaction_hash")


def _evidence(info: dict | None = None, trades: list | None = None) -> dict:
    """Whitelisted venue fields only: raw payloads carry maker addresses / API-key owners."""
    out: dict = {}
    if info is not None:
        out["order"] = {k: info.get(k) for k in ORDER_FIELDS if k in info}
    if trades is not None:
        out["trades"] = [{k: t.get(k) for k in TRADE_FIELDS if k in t} for t in trades if t]
    return out


def _our_bucket(trade: dict, order_id: str) -> tuple[float, float, bool] | None:
    """(shares, price, is_taker) of our side of a trade."""
    if trade.get("taker_order_id") == order_id:
        return float(trade["size"]), float(trade["price"]), True
    for m in trade.get("maker_orders") or []:
        if m.get("order_id") == order_id:
            return float(m["matched_amount"]), float(m["price"]), False
    return None


def reconcile_order(ledger: StrategyLedger, clob, order, now: int,
                    fee_lookup: Callable[[str], FeeSchedule | None]) -> str:
    """Advance one pending live order. Returns the resulting order status."""
    age = now - int(order["created_at"])
    pos = ledger.position(order["position_id"]) if order["position_id"] else None
    oid = order["exchange_order_id"]

    if not oid:
        # intent/unknown without an order id: the POST outcome is unknowable from the venue
        if age >= QUARANTINE_AFTER_S and pos is not None and pos["status"] not in ("quarantined", "closed", "resolved"):
            ledger.quarantine(pos["position_id"], now, f"{order['side'].lower()}_submit_outcome_unknown_3h")
        return order["status"]

    info = clob.order(oid)
    trade_ids = list((info or {}).get("associate_trades") or [])
    if info is None:
        trade_ids = [t["id"] for t in clob.token_trades(order["token_id"]) if _our_bucket(t, oid)]
    size_matched = float((info or {}).get("size_matched") or 0)
    ostatus = str((info or {}).get("status") or "").upper()

    if not trade_ids:
        zero_proven = (info is not None and ostatus in TERMINAL_ORDER and size_matched == 0) \
            or (info is None and age >= FOK_ZERO_FILL_DELAY_S)
        if not zero_proven and age >= FOK_ZERO_FILL_DELAY_S and info is not None:
            try:
                clob.cancel(oid)
            except Exception:
                pass
            info2 = clob.order(oid) or {}
            zero_proven = float(info2.get("size_matched") or 0) == 0 and \
                str(info2.get("status") or "").upper() in TERMINAL_ORDER
        if zero_proven:
            ledger.update_order(order["intent_id"], now, "cancelled", response=_evidence(info))
            if pos is not None:
                if order["side"] == "BUY":
                    ledger.mark_unfilled(pos["position_id"], now, "fok_zero_fill")
                else:
                    ledger.set_position(pos["position_id"], status="open", exit_reason=None)
            return "cancelled"
        return _maybe_quarantine(ledger, order, pos, now, age)

    trades = [clob.trade(t) for t in trade_ids]
    if any(t is None for t in trades):
        return _maybe_quarantine(ledger, order, pos, now, age)
    statuses = {str(t.get("status") or "").upper() for t in trades}
    if not statuses <= {"CONFIRMED", "FAILED"}:
        return _maybe_quarantine(ledger, order, pos, now, age)

    schedule = fee_lookup(order["condition_id"]) if order["condition_id"] else None
    confirmed = []
    for t in trades:
        if str(t.get("status")).upper() != "CONFIRMED":
            continue
        bucket = _our_bucket(t, oid)
        if bucket is None:
            return _maybe_quarantine(ledger, order, pos, now, age)
        shares, price, is_taker = bucket
        fee = fee_usdc(schedule, shares, price, taker=is_taker)
        if fee is None:
            return _maybe_quarantine(ledger, order, pos, now, age)   # unknown fee: never assume 0
        confirmed.append((t, shares, price, fee))

    if not confirmed:   # every trade FAILED
        ledger.update_order(order["intent_id"], now, "cancelled", response=_evidence(trades=trades))
        if pos is not None:
            if order["side"] == "BUY":
                ledger.mark_unfilled(pos["position_id"], now, "all_trades_failed")
            else:
                ledger.set_position(pos["position_id"], status="open", exit_reason=None)
        return "cancelled"
    if len(confirmed) != len(trades):
        # mixed CONFIRMED/FAILED is a reconciliation error: keep evidence, stay blocked
        return _maybe_quarantine(ledger, order, pos, now, age)
    total = sum(c[1] for c in confirmed)
    if size_matched and abs(total - size_matched) > 1e-6:
        return _maybe_quarantine(ledger, order, pos, now, age)

    usd = sum(c[1] * c[2] for c in confirmed)
    if order["side"] == "BUY" and order["usdc_amount"] is not None and usd > float(order["usdc_amount"]) + 0.01:
        return _maybe_quarantine(ledger, order, pos, now, age)      # outside the signed maker envelope

    for t, shares, price, fee in confirmed:
        ledger.record_fill(fill_id=f"{t['id']}:{oid}", intent_id=order["intent_id"], ts=now, side=order["side"],
                           price=price, shares=shares, fee_usdc=fee, status="CONFIRMED",
                           raw={k: t.get(k) for k in TRADE_FIELDS if k in t})
    fees = round(sum(c[3] for c in confirmed), 6)
    ledger.update_order(order["intent_id"], now, "confirmed")
    if pos is not None:
        if order["side"] == "BUY":
            ledger.apply_buy(pos["position_id"], total, usd, fees)
        else:
            kind = (pos["exit_reason"] or "exit").removeprefix("partial_")
            ledger.apply_sell(pos["position_id"], total, usd, fees, kind, now, "confirmed_sell")
    return "confirmed"


def _maybe_quarantine(ledger, order, pos, now, age) -> str:
    if age >= QUARANTINE_AFTER_S and pos is not None and pos["status"] not in ("quarantined", "closed", "resolved"):
        ledger.quarantine(pos["position_id"], now,
                          f"{order['side'].lower()}_reconciliation_timeout_3h_unknown_exposure")
    return order["status"]


def reconcile_live(ledger: StrategyLedger, clob, now: int, fee_lookup) -> dict:
    out: dict[str, int] = {}
    for order in ledger.pending_orders("live"):
        try:
            st = reconcile_order(ledger, clob, order, now, fee_lookup)
        except Exception as e:  # one bad order must not stop the others, but it still ages into quarantine
            st = f"error:{type(e).__name__}"
            pos = ledger.position(order["position_id"]) if order["position_id"] else None
            _maybe_quarantine(ledger, order, pos, now, now - int(order["created_at"]))
        out[st] = out.get(st, 0) + 1
    return out


def settle_resolutions(ledger: StrategyLedger, view: MarketView, now: int, mode: str) -> int:
    """Close open positions whose market resolved at or before now (exact outcome index).

    Checked every cycle (legacy only looked when the book vanished). A void (0.5/0.5)
    resolution is not representable in core.db yet; such markets simply stay open.
    """
    n = 0
    for pos in ledger.positions(mode, ("open",)):
        idx = view.resolution(pos["condition_id"], now)
        if idx is None:
            continue
        market = view.market(pos["condition_id"])
        tok = next((t for t in (market.tokens if market else ()) if t.token_id == pos["token_id"]), None)
        if tok is None:
            continue
        ledger.apply_resolution(pos["position_id"], 1.0 if tok.outcome_index == idx else 0.0, now,
                                redeemable=(mode == "live"))
        n += 1
    return n
