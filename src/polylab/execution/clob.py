"""CLOB trading client (py-clob-client-v2), FOK only.

Order flow for the engine: `sign_fok_buy/sign_fok_sell` (no network side effects beyond
read-only tick/market lookups) -> ledger.record_intent (committed) -> `post` -> response.
Follows docs/strategies/execution-legacy-notes.md §2:
- derive_api_key() only (never mint a key per cycle),
- exact-USDC MarketOrderArgs BUY with an explicit limit, walking up the tick grid to the
  band ceiling until maker == amount*1e6 and taker has <= 4 decimals,
- SELL = OrderArgs FOK at the worst bid needed, shares floored to 0.01 (nextafter nudge).
The private key and funder are never logged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from polylab.execution.fees import FeeSchedule, parse_fee_schedule
from polylab.marketview import Book, make_book

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
TAKER_QUANTUM_MICROS = 100


class PreSubmissionError(RuntimeError):
    """Proven no-POST: safe to retry later."""


@dataclass
class SignedOrder:
    order: Any
    side: str
    token_id: str
    price: float
    maker_amount: float        # BUY: USDC / SELL: shares
    taker_amount: float        # BUY: shares / SELL: USDC


def build_client(creds, host: str = CLOB_HOST, client_cls=None):
    """Authenticated ClobClient (L2) from settings.AccountCredentials."""
    if client_cls is None:
        from py_clob_client_v2 import ClobClient as client_cls
    if creds.signature_type not in (1, 2, 3):
        raise ValueError("unsupported signature type")
    client = client_cls(host=host, chain_id=CHAIN_ID, key=creds.private_key,
                        signature_type=creds.signature_type, funder=creds.funder_address)
    client.set_api_creds(client.derive_api_key())
    return client


def _round_tick(price: float, tick: float, up: bool) -> float:
    q = Decimal(str(price)) / Decimal(str(tick))
    q = q.to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return float(q * Decimal(str(tick)))


def floor2(x: float) -> float:
    return math.floor(x * 100 + 1e-9) / 100.0


class Clob:
    def __init__(self, client):
        self.client = client

    # ------------------------------------------------------------ read
    def book(self, token_id: str, now: int) -> Book | None:
        raw = self.client.get_order_book(token_id)
        if raw is None:
            return None
        get = raw.get if isinstance(raw, dict) else (lambda k, d=None: getattr(raw, k, d))
        return make_book(token_id, now, get("bids") or [], get("asks") or [], "clob_live")

    def market_accepting(self, condition_id: str) -> bool:
        """Cheap equivalent of the legacy OPEN proof: never SELL into a closed market's dust bids."""
        m = self.client.get_market(condition_id) or {}
        return bool(m.get("accepting_orders", m.get("acceptingOrders"))) and not m.get("closed") \
            and m.get("active", True) is not False

    def fee_schedule(self, condition_id: str) -> FeeSchedule | None:
        """Raw CLOB `fd` (the SDK defaults a missing fd to 0 — we don't)."""
        raw = self.client.get_clob_market_info(condition_id) or {}
        fd = raw.get("fd")
        return parse_fee_schedule(fd) if isinstance(fd, dict) and fd else None

    def tick_size(self, token_id: str) -> float:
        return float(self.client.get_tick_size(token_id))

    def order(self, order_id: str) -> dict | None:
        """None only on a definite 404; transient errors propagate (never read as 'no order')."""
        try:
            return self.client.get_order(order_id)
        except Exception as e:
            if getattr(e, "status_code", None) == 404:
                return None
            raise

    def trade(self, trade_id: str) -> dict | None:
        from py_clob_client_v2 import TradeParams
        rows = self.client.get_trades(TradeParams(id=trade_id), only_first_page=True) or []
        return next((t for t in rows if t.get("id") == trade_id), None)

    def token_trades(self, token_id: str) -> list[dict]:
        from py_clob_client_v2 import TradeParams
        return self.client.get_trades(TradeParams(asset_id=token_id), only_first_page=False) or []

    def cancel(self, order_id: str) -> Any:
        return self.client.cancel_orders([order_id])

    # ------------------------------------------------------------ sign
    def sign_fok_buy(self, token_id: str, amount_usdc: float, limit_price: float, max_price: float) -> SignedOrder:
        from py_clob_client_v2 import MarketOrderArgs, OrderType
        from py_clob_client_v2.clob_types import PartialCreateOrderOptions

        amount = Decimal(str(amount_usdc))
        if amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise PreSubmissionError("BUY amount must be positive with <= 2 decimals")
        if not (0 < limit_price < 1 and 0 < max_price < 1) or max_price + 1e-12 < limit_price:
            raise PreSubmissionError("limit outside (0, 1) or above ceiling")
        try:
            tick = self.tick_size(token_id)
        except Exception as e:  # read-only preflight: no POST happened
            raise PreSubmissionError(f"tick size unavailable: {type(e).__name__}") from None
        price = Decimal(str(_round_tick(limit_price, tick, up=True)))
        ceiling = Decimal(str(_round_tick(max_price, tick, up=False)))
        if price > ceiling:
            raise PreSubmissionError("fresh limit exceeds band ceiling")
        expected_maker = int(amount * 1_000_000)
        step = Decimal(str(tick))
        while price <= ceiling:
            args = MarketOrderArgs(token_id=token_id, amount=float(amount), side="BUY", price=float(price),
                                   order_type=OrderType.FOK)   # user_usdc_balance left 0: no fee shrink
            opts = PartialCreateOrderOptions(tick_size="0.01") \
                if step < Decimal("0.01") and price == price.quantize(Decimal("0.01")) else None
            signed = self.client.create_market_order(args, options=opts) if opts else self.client.create_market_order(args)
            maker, taker = int(str(signed.makerAmount)), int(str(signed.takerAmount))
            if maker == expected_maker and taker % TAKER_QUANTUM_MICROS == 0:
                return SignedOrder(signed, "BUY", token_id, float(price), maker / 1e6, taker / 1e6)
            price += step
        raise PreSubmissionError("no signable price inside the band keeps exact maker/taker precision")

    def sign_fok_sell(self, token_id: str, shares: float, limit_price: float) -> SignedOrder:
        from py_clob_client_v2 import OrderArgs

        size = floor2(shares)
        if size <= 0:
            raise PreSubmissionError("nothing sellable after 0.01 floor")
        if not 0 < limit_price < 1:
            raise PreSubmissionError("limit outside (0, 1)")
        try:
            tick = self.tick_size(token_id)
        except Exception as e:
            raise PreSubmissionError(f"tick size unavailable: {type(e).__name__}") from None
        price = _round_tick(limit_price, tick, up=False)
        if price <= 0:
            raise PreSubmissionError("limit rounds to zero")
        # nextafter avoids a float double-floor inside the SDK turning 5.37 into 5.36
        signed = self.client.create_order(OrderArgs(token_id=token_id, price=price,
                                                    size=math.nextafter(size, math.inf), side="SELL"))
        signed_shares = int(str(signed.makerAmount)) / 1e6
        if not (0 <= size - signed_shares < 0.01):
            raise PreSubmissionError("signed SELL size drifted from requested")
        return SignedOrder(signed, "SELL", token_id, price, signed_shares, int(str(signed.takerAmount)) / 1e6)

    # ------------------------------------------------------------ post
    def post(self, signed: SignedOrder) -> dict:
        from py_clob_client_v2 import OrderType
        resp = self.client.post_order(signed.order, OrderType.FOK)
        return resp if isinstance(resp, dict) else {"raw": str(resp)}


def classify_post_response(resp: dict) -> tuple[str, str | None]:
    """-> (order status, exchange order id). Anomalies are 'unknown' (token×side blocked)."""
    order_id = resp.get("orderID") or resp.get("orderId") or resp.get("id")
    success = resp.get("success")
    if success is True and order_id:
        status = str(resp.get("status") or "").lower()
        return ("matched" if status in ("matched", "mined", "confirmed") else "posted"), order_id
    if success is False and not order_id:
        return "failed", None
    return "unknown", order_id


def classify_post_error(exc: BaseException) -> str:
    """Explicit venue rejection (4xx) proves no order -> 'failed'; anything else -> 'unknown'."""
    code = getattr(exc, "status_code", None)
    if isinstance(code, int) and 400 <= code < 500 and code not in (408, 425, 429):
        return "failed"
    return "unknown"
