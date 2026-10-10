"""Maker (resting GTC post-only) order lifecycle — 2026-10-05 owner decision `fees:maker-preferred`.

Polymarket sports fees are taker-only; a resting order that someone else hits pays no fee. A variant opts in
with `order_style: maker` (per sport via `sport_overrides`). Lifecycle in the strategy DB (`orders.order_type
= 'GTC'`, never touched by the FOK reconciler):

  intent -> resting -> confirmed (fully filled, every trade CONFIRMED)
                    -> cancel_requested -> cancelled (venue CANCELED; partial CONFIRMED fills are kept)
         -> failed (explicit rejection, e.g. a post-only order that would cross)
  intent|unknown without an order id: blocks its token×side; the open-order sweep adopts it by exact
  (token, side, price, size), otherwise its position is quarantined after 3 h.

Entries: a BUY rests at `maker_entry_price` (default min(best_bid + tick, best_ask - tick), our own quote removed
from the book first). Each tick: fills are reconciled (CONFIRMED only), the order is cancelled when the entry
window closes / the kill switch is on / the market stops trading, and cancelled-and-replaced on TTL or when the
target drifted more than `maker_reprice_ticks`. The position stays `pending` until the entry order is terminal,
then becomes `open` (shares > 0) or `unfilled`.
Take-profit: an open position rests a SELL at its TP price while the strategy allows it (goal_over: before
kickoff only, so the goal jump to >= 0.99 is ridden to resolution as the hold rule says). A stop-loss first
cancels the resting TP, waits for the venue to confirm the cancel, books any TP fills, then sells the remainder
as taker (the engine does that part).
Fees: a fill is fee-free only when the venue lists our order among the trade's `maker_orders` and the fee
schedule is taker-only; an unknown schedule leaves the fill unbooked (never a silent zero). Maker rebates are
paid out-of-band by Polymarket and are not visible per trade, so they are not booked.
Paper: a resting BUY fills only when a REAL (non-synthetic) book observed after the order (and after its last
fill) has asks strictly below our price — trade-through, queue position unknown — up to the depth through it;
a SELL symmetric on bids. Fill delay is recorded (book ts - created_at). Paper therefore never flatters maker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from polylab.execution import clob as clobmod
from polylab.execution.fees import FeeSchedule, fee_usdc
from polylab.execution.ledger import DUST_SHARES, MAKER_ACTIVE, StrategyLedger
from polylab.execution.reconcile import QUARANTINE_AFTER_S, TRADE_FIELDS, _our_bucket
from polylab.marketview import EPS, Book
from polylab.strategies.base import floor2

DEFAULT_MIN_SHARES = 5.0
DEFAULT_TICK = 0.01
PAPER_BOOK_MAX_AGE_S = 900
VENUE_DONE = {"MATCHED", "CANCELED", "CANCELLED", "CANCELED_MARKET_RESOLVED", "INVALID"}
VENUE_CANCELLED = VENUE_DONE - {"MATCHED"}
ORDER_FIELDS = ("id", "status", "size_matched", "original_size", "price", "side", "created_at", "order_type")


@dataclass
class Fill:
    fill_id: str
    shares: float
    price: float
    fee: float | None
    taker_fee_est: float | None
    liquidity: str
    match_ts: int | None
    raw: dict = field(default_factory=dict)


@dataclass
class Poll:
    """Venue view of one maker order: CONFIRMED fills so far (idempotent by fill_id) and whether it is done."""
    state: str                    # live | done | unknown
    fills: list[Fill] = field(default_factory=list)
    problem: str | None = None
    evidence: dict | None = None


# ---------------------------------------------------------------- pricing

def own_removed(book: Book | None, side: str, price: float | None, shares: float) -> Book | None:
    """The live book minus our own resting quote (else the improve rule ratchets against itself)."""
    if book is None or price is None or shares <= 0:
        return book
    levels = book.bids if side == "BUY" else book.asks
    out = []
    for p, s in levels:
        if abs(p - price) < 1e-9:
            s = s - shares
            if s <= 1e-9:
                continue
        out.append((p, s))
    return Book(book.token_id, book.ts, out if side == "BUY" else book.bids,
                book.asks if side == "BUY" else out, book.source, book.synthetic)


def entry_price(book: Book | None, tick: float, lo: float, hi: float, rule: str = "improve") -> float | None:
    """Resting BUY price: `improve` = min(best_bid + tick, best_ask - tick) (one tick inside, never crossing),
    `join` = best_bid, `below` = best_bid - tick (behind the queue: cheaper, fills later). None without a two-sided
    book or outside [lo, hi]."""
    if book is None or book.best_bid is None or book.best_ask is None or book.crossed:
        return None
    bid, ask = book.best_bid, book.best_ask
    px = bid if rule == "join" else bid - tick if rule == "below" else min(bid + tick, ask - tick)
    px = round(round(px / tick) * tick, 6)
    if px < tick - 1e-12 or px >= ask - 1e-9:
        return None
    if not (lo - EPS <= px <= hi + EPS):
        return None
    return px


# ---------------------------------------------------------------- venues

class LiveVenue:
    mode = "live"

    def __init__(self, clob: clobmod.Clob, fee_lookup):
        self.clob = clob
        self.fee_lookup = fee_lookup
        self.open_by_id: dict[str, dict] | None = None

    def tick(self, token_id: str) -> float:
        return float(self.clob.tick_size(token_id))

    def min_shares(self, token_id: str) -> float:
        return float(self.clob.min_order_size(token_id) or DEFAULT_MIN_SHARES)

    def book(self, token_id: str, now: int) -> Book | None:
        return self.clob.book(token_id, now)

    def prefetch(self) -> list[dict]:
        rows = self.clob.open_orders()
        self.open_by_id = {str(r.get("id")): r for r in rows if r.get("id")}
        return rows

    def post(self, ledger: StrategyLedger, *, position_id: str, side: str, token_id: str, condition_id: str | None,
             price: float, shares: float, now: int, purpose: str) -> tuple[str, str | None]:
        try:
            signed = self.clob.sign_limit(token_id, side, price, shares)
        except clobmod.PreSubmissionError as e:
            return f"no_post:{str(e)[:80]}", None
        iid = ledger.record_intent(position_id=position_id, mode="live", side=side, token_id=token_id,
                                   condition_id=condition_id, now=now, usdc_amount=round(signed.maker_amount, 6)
                                   if side == "BUY" else None, shares=signed.taker_amount if side == "BUY"
                                   else signed.maker_amount, limit_price=signed.price, order_type="GTC",
                                   purpose=purpose, post_only=True)
        try:
            resp = self.clob.post_gtc(signed, post_only=True)
        except Exception as e:
            status = clobmod.classify_post_error(e)
            from polylab.engine.tick import sanitize  # noqa: PLC0415
            ledger.update_order(iid, now, status, response={"error": sanitize(e)})
            return status, iid
        status, oid = clobmod.classify_gtc_response(resp)
        ledger.update_order(iid, now, status, oid, {k: resp.get(k) for k in ("success", "status", "orderID",
                                                                             "errorMsg") if k in resp})
        return status, iid

    def cancel(self, order) -> None:
        oid = order["exchange_order_id"]
        if oid:
            self.clob.cancel(oid)
            if self.open_by_id is not None:
                self.open_by_id.pop(oid, None)       # the next poll must ask the venue, not the prefetch

    def poll(self, order, now: int) -> Poll:
        oid = order["exchange_order_id"]
        if not oid:
            return Poll("unknown", problem="no_order_id")
        known = float(order["filled_shares"] or 0)
        if self.open_by_id is not None and oid in self.open_by_id:
            o = self.open_by_id[oid]
            if abs(float(o.get("size_matched") or 0) - known) < 1e-9:
                return Poll("live")          # still resting, nothing new matched: no per-order calls
        info = self.clob.order(oid)
        if info is None:
            return Poll("unknown", problem="order_404")
        st = str(info.get("status") or "").upper()
        size_matched = float(info.get("size_matched") or 0)
        trade_ids = list(info.get("associate_trades") or [])
        if size_matched > 0 and not trade_ids:
            trade_ids = [t["id"] for t in self.clob.token_trades(order["token_id"]) if _our_bucket(t, oid)]
        schedule: FeeSchedule | None = self.fee_lookup(order["condition_id"]) if order["condition_id"] else None
        fills, pending, problem, trades = [], False, None, []
        for tid in trade_ids:
            t = self.clob.trade(tid)
            if t is None:
                pending = True
                continue
            trades.append(t)
            ts = str(t.get("status") or "").upper()
            if ts == "FAILED":
                continue
            if ts != "CONFIRMED":
                pending = True
                continue
            bucket = _our_bucket(t, oid)
            if bucket is None:
                problem, pending = "trade_without_our_order", True
                continue
            shares, price, is_taker = bucket
            side_tag = str(t.get("trader_side") or "").upper()
            if side_tag in ("MAKER", "TAKER") and (side_tag == "TAKER") != is_taker:
                problem, pending = "liquidity_role_conflict", True
                continue
            fee = fee_usdc(schedule, shares, price, taker=is_taker)
            if fee is None:
                problem, pending = "fee_schedule_unknown", True
                continue
            fills.append(Fill(f"{tid}:{oid}", shares, price, fee, fee_usdc(schedule, shares, price, taker=True),
                              "taker" if is_taker else "maker", _int(t.get("match_time")),
                              {k: t.get(k) for k in (*TRADE_FIELDS, "fee_rate_bps") if k in t}))
        ev = {"order": {k: info.get(k) for k in ORDER_FIELDS if k in info},
              "trades": [{k: t.get(k) for k in TRADE_FIELDS if k in t} for t in trades]}
        done = st in VENUE_DONE and not pending
        if done and abs(sum(f.shares for f in fills) - size_matched) > 1e-6:
            done, problem = False, problem or "size_matched_mismatch"
        # venue-done but trades not yet CONFIRMED: keep waiting (MATCHED/MINED are not terminal)
        return Poll("done" if done else "live", fills, problem, ev)


class PaperVenue:
    mode = "paper"

    def __init__(self, view, fee_lookup):
        self.view = view
        self.fee_lookup = fee_lookup
        self.open_by_id = None
        self._ticks: dict[str, float] = {}

    def tick(self, token_id: str) -> float:
        """The venue's tick as seen in the last stored book: Polymarket quotes 0.001 steps near 0 and 1 (stored levels
        then carry a third decimal), else 0.01. Without it "one tick below the bid" would round to a whole cent."""
        return self._ticks.get(token_id, DEFAULT_TICK)

    def min_shares(self, token_id: str) -> float:
        return DEFAULT_MIN_SHARES

    def book(self, token_id: str, now: int) -> Book | None:
        # pre-game Total 0.5 books are stored about every 10 min; 180 s left most pre-game minutes without a price
        b = self.view.book(token_id, now, max_age_s=PAPER_BOOK_MAX_AGE_S)
        if b is not None:
            fine = any(abs(p * 100 - round(p * 100)) > 1e-6 for p, _ in (b.bids or []) + (b.asks or []))
            self._ticks[token_id] = 0.001 if fine else DEFAULT_TICK
        return b

    def prefetch(self) -> list[dict]:
        return []

    def post(self, ledger: StrategyLedger, *, position_id: str, side: str, token_id: str, condition_id: str | None,
             price: float, shares: float, now: int, purpose: str) -> tuple[str, str | None]:
        size = floor2(shares)
        if size <= 0:
            return "no_post:size", None
        iid = ledger.record_intent(position_id=position_id, mode="paper", side=side, token_id=token_id,
                                   condition_id=condition_id, now=now,
                                   usdc_amount=round(size * price, 6) if side == "BUY" else None, shares=size,
                                   limit_price=price, order_type="GTC", purpose=purpose, post_only=True)
        ledger.update_order(iid, now, "resting", f"paper:{iid}")
        return "resting", iid

    def cancel(self, order) -> None:
        pass  # simulated venue: the next poll sees it cancelled

    def poll(self, order, now: int, cancelled: bool = False) -> Poll:
        remaining = floor2(float(order["shares"] or 0) - float(order["filled_shares"] or 0))
        if order["status"] == "cancel_requested" or cancelled:
            return Poll("done")
        if remaining <= 0:
            return Poll("done")
        book = self.view.book(order["token_id"], now, max_age_s=180)
        last = ledger_last_fill_ts(order)
        if book is None or book.synthetic or book.ts <= max(int(order["created_at"]), last):
            return Poll("live")
        px = float(order["limit_price"])
        if order["side"] == "BUY":
            through = sum(s for p, s in book.asks if p < px - EPS)
        else:
            through = sum(s for p, s in book.bids if p > px + EPS)
        qty = floor2(min(remaining, through))
        if qty <= 0:
            return Poll("live")
        sched = self.fee_lookup(order["condition_id"]) if order["condition_id"] else None
        f = Fill(f"{order['intent_id']}:{book.ts}", qty, px, fee_usdc(sched, qty, px, taker=False),
                 fee_usdc(sched, qty, px, taker=True), "maker", book.ts)
        return Poll("done" if qty >= remaining - 1e-9 else "live", [f])


def ledger_last_fill_ts(order) -> int:
    try:
        return int(order["last_fill_ts"] or 0)
    except (IndexError, KeyError):
        return 0


def _int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- shared lifecycle

def _order_with_last_fill(ledger: StrategyLedger, intent_id: str):
    return ledger.conn.execute(
        "SELECT o.*, (SELECT MAX(COALESCE(f.match_ts, f.ts)) FROM fills f WHERE f.intent_id=o.intent_id) "
        "AS last_fill_ts FROM orders o WHERE o.intent_id=?", (intent_id,)).fetchone()


def sync(ledger: StrategyLedger, venue, order, now: int, finalize: bool = True) -> str:
    """Book new CONFIRMED fills of one maker order and finalize it when the venue is done (`finalize=False`:
    leave a pending entry position pending — the caller re-quotes it). Returns the order's ledger status."""
    order = _order_with_last_fill(ledger, order["intent_id"])
    poll = venue.poll(order, now)
    pos = ledger.position(order["position_id"]) if order["position_id"] else None
    for f in poll.fills:
        if not ledger.record_fill(fill_id=f.fill_id, intent_id=order["intent_id"], ts=now, side=order["side"],
                                  price=f.price, shares=f.shares, fee_usdc=f.fee,
                                  status="CONFIRMED" if venue.mode == "live" else "PAPER", raw=f.raw or None,
                                  liquidity=f.liquidity, taker_fee_est=f.taker_fee_est, match_ts=f.match_ts):
            continue                                     # already booked on an earlier tick
        ledger.note_maker_fill(order["intent_id"], f.shares, f.shares * f.price, now)
        if pos is None:
            continue
        if order["side"] == "BUY":
            ledger.add_buy_fill(pos["position_id"], f.shares, f.shares * f.price, f.fee)
        else:
            ledger.apply_sell(pos["position_id"], f.shares, f.shares * f.price, f.fee, "take_profit", now,
                              "confirmed_sell" if venue.mode == "live" else "paper")
        pos = ledger.position(pos["position_id"])
    if poll.problem:
        # evidence kept on the order; it stays active (blocking its token×side) until the venue state is clear
        ledger.update_order(order["intent_id"], now, order["status"], response={"problem": poll.problem,
                                                                                  **(poll.evidence or {})})
        if poll.problem == "no_order_id" and now - int(order["created_at"]) >= QUARANTINE_AFTER_S \
                and pos is not None and pos["status"] not in ("quarantined", "closed", "resolved", "unfilled"):
            ledger.quarantine(pos["position_id"], now, f"maker_{order['side'].lower()}_submit_outcome_unknown_3h")
        return order["status"]
    if poll.state != "done":
        return order["status"]
    order = ledger.order(order["intent_id"])
    filled = float(order["filled_shares"] or 0)
    full = filled >= float(order["shares"] or 0) - 0.01
    ledger.update_order(order["intent_id"], now, "confirmed" if full else "cancelled",
                        response=poll.evidence or None)
    if pos is not None and order["side"] == "BUY" and finalize:
        finalize_entry(ledger, pos["position_id"], now)
    return "confirmed" if full else "cancelled"


def finalize_entry(ledger: StrategyLedger, position_id: str, now: int) -> None:
    """Entry order terminal: pending -> open (some CONFIRMED shares) or unfilled (none)."""
    pos = ledger.position(position_id)
    if pos is None or pos["status"] != "pending":
        return
    if any(o["status"] in MAKER_ACTIVE for o in ledger.conn.execute(
            "SELECT status FROM orders WHERE position_id=? AND side='BUY'", (position_id,))):
        return
    if float(pos["shares"] or 0) >= DUST_SHARES:
        ledger.set_position(position_id, status="open")
    else:
        ledger.mark_unfilled(position_id, now, "maker_unfilled")


def request_cancel(ledger: StrategyLedger, venue, order, now: int, reason: str, finalize: bool = True) -> bool:
    """Cancel by id, then confirm with the venue. True only when the order is terminal (fills booked)."""
    if order["status"] in ("intent", "unknown") and not order["exchange_order_id"]:
        return False                                      # nothing to cancel by id yet (sweep may adopt it)
    try:
        venue.cancel(order)                                # idempotent by id: re-sent until the venue confirms
    except Exception as e:  # transport error: retry next tick, the order may still rest
        ledger.update_order(order["intent_id"], now, order["status"], response={"cancel_error": type(e).__name__})
        return False
    if order["status"] != "cancel_requested":
        ledger.set_order_fields(order["intent_id"], now, status="cancel_requested", cancel_reason=reason)
    return sync(ledger, venue, ledger.order(order["intent_id"]), now, finalize) in ("cancelled", "confirmed")


def sweep(ledger: StrategyLedger, venue, now: int) -> dict:
    """Live: reconcile the account's open orders with this ledger.
    - ours but terminal in the ledger -> cancel (an orphan; never by market/all, only by id)
    - an intent/unknown row without an id matching exactly (token, side, price, size) -> adopt it
    - anything else is another owner's order (other variant / manual): counted, never touched."""
    out = {"orphans_cancelled": 0, "adopted": 0, "foreign": 0}
    if venue.mode != "live":
        return out
    rows = venue.prefetch()
    known = {r["exchange_order_id"]: r for r in ledger.conn.execute(
        "SELECT * FROM orders WHERE order_type='GTC' AND exchange_order_id IS NOT NULL")}
    loose = [r for r in ledger.maker_orders("live") if not r["exchange_order_id"]]
    for o in rows:
        oid = str(o.get("id"))
        row = known.get(oid)
        if row is not None:
            if row["status"] not in ("resting", "cancel_requested", "intent", "unknown"):
                try:
                    venue.clob.cancel(oid)
                    out["orphans_cancelled"] += 1
                except Exception:
                    pass
            continue
        match = next((r for r in loose if r["token_id"] == o.get("asset_id")
                      and r["side"] == str(o.get("side") or "").upper()
                      and abs(float(r["limit_price"] or 0) - float(o.get("price") or -1)) < 1e-9
                      and abs(float(r["shares"] or 0) - float(o.get("original_size") or -1)) < 1e-6), None)
        if match is not None:
            ledger.update_order(match["intent_id"], now, "resting", oid, {"adopted_from_open_orders": True})
            loose.remove(match)
            out["adopted"] += 1
        else:
            out["foreign"] += 1
    return out
