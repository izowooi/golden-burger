"""Paper broker: FOK fills against a (stored) book by walking full depth.

FOK semantics: only levels at or better than the limit count; if the whole amount cannot
be filled there, nothing fills. Fees use the known schedule; unknown -> fee None (flagged).
"""

from __future__ import annotations

from dataclasses import dataclass

from polylab.execution.fees import FeeSchedule, fee_usdc
from polylab.marketview import EPS, Book, walk_asks, walk_bids


@dataclass(frozen=True)
class PaperFill:
    filled: bool
    shares: float
    price: float | None          # VWAP
    usd: float                   # BUY: spent ex-fee / SELL: gross proceeds
    fee_usdc: float | None
    reason: str


def paper_buy(book: Book | None, usdc: float, limit_price: float, fee: FeeSchedule | None) -> PaperFill:
    if book is None:
        return PaperFill(False, 0.0, None, 0.0, None, "no_book")
    asks = [(p, s) for p, s in book.asks if p <= limit_price + EPS]
    w = walk_asks(asks, usdc)
    if not w.ok:
        return PaperFill(False, 0.0, None, 0.0, None, "fok_unfillable")
    return PaperFill(True, w.shares, w.vwap, w.usd, fee_usdc(fee, w.shares, w.vwap), "filled")


def paper_sell(book: Book | None, shares: float, limit_price: float, fee: FeeSchedule | None) -> PaperFill:
    if book is None:
        return PaperFill(False, 0.0, None, 0.0, None, "no_book")
    bids = [(p, s) for p, s in book.bids if p + EPS >= limit_price]
    w = walk_bids(bids, shares)
    if not w.ok:
        return PaperFill(False, 0.0, None, 0.0, None, "fok_unfillable")
    return PaperFill(True, w.shares, w.vwap, w.usd, fee_usdc(fee, w.shares, w.vwap), "filled")
