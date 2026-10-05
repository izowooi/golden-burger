"""Cherry "back to basics" (2026-10-05 owner decision `cherry:back-to-basics`): near-resolution favourites of every
Gamma category.

Entry (one per market, ever): a data/general market whose reference end (`general.store.end_ref`: kickoff + 3 h
for game markets, else Gamma endDate) is `hours_to_end_min`–`hours_to_end_max` hours away, that was listed at least
`min_listed_hours` before that end, passes the volume/liquidity floors and the category filter, and whose LEADING
outcome (the side priced >= 0.5; binary books mirror) is inside [entry_min, entry_max]. The order is a FOK taker
buy walked at most to entry_max (taker fee). Markets core.db tracks are read through core (same view); the rest
through data/general (MarketView's core-miss fallback).

Exits (frozen into exit_rules at entry, "v": 2):
1. hold_above_price (optional): best bid at/above it -> no exit at all, ride to resolution.
2. take-profit: sell the whole holding once its bid VWAP >= min(entry + take_profit_delta, take_profit_price)
   (whichever are set) and the sale is net-positive after buy+sell fees (unknown fee never passes).
3. time exit (optional): `time_exit_hours_before_end` hours before end_ref, sell at the bid (floor bid - 0.03).
No stop loss, no trailing. Otherwise held to the exact resolution.

Positions opened before 2026-10-05 (sports-game cherry: no "v" key) keep their frozen legacy rules: absolute TP
(2026-10-04 keys) and the midpoint chain SL -> relative TP -> trailing where those are set.
Backtest and parameter choice: docs/research/backtests/2026-10-05-cherry-basics.md (polylab.analysis.cherry_basics).
"""

from __future__ import annotations

from polylab.execution.fees import FeeSchedule, parse_fee_schedule
from polylab.marketview import EPS, Book, MarketView
from polylab.strategies.base import (Check, EntryIntent, ExitIntent, Ledger, PositionView, Strategy, band_walk_check,
                                     floor2, net_positive_tp_check)

RULES_VERSION = 2
TIME_EXIT_SLIPPAGE = 0.03

DEFAULTS = {
    "hours_to_end_min": 48.0,
    "hours_to_end_max": 72.0,
    "min_listed_hours": 72.0,           # market listed at least this long before its end_ref
    "entry_min": 0.90,                  # leading-outcome price band (signal on mid, FOK walk capped at entry_max)
    "entry_max": 0.95,
    "take_profit_delta": None,          # TP at bid VWAP >= entry + delta; None = off
    "take_profit_price": 0.95,          # TP at a fixed bid VWAP; None = off
    "hold_above_price": 0.99,           # best bid at/above -> hold to resolution; None = off
    "time_exit_hours_before_end": None,  # sell this many hours before end_ref; None = hold to resolution
    "min_volume": 10000.0,              # Gamma volumeNum (as of the latest discover run)
    "min_liquidity": 0.0,               # Gamma liquidityNum (live only: not reproducible historically)
    "categories": None,                 # e.g. ["sports"]; None = every category
    "exclude_categories": [],
    "include_core_sports": True,        # also the 5-sport game markets core.db tracks
    "min_ask_depth_usd": None,          # optional book-depth floor inside the band
    "price_max_age_s": 900,
    "book_max_age_s": 300,
    # legacy keys read only by pre-2026-10-05 positions' frozen rules
    "take_profit_percent": None,
    "stop_loss_percent": None,
    "trailing_enabled": False,
    "trailing_percent": 0.15,
}


def _num(x) -> float | None:
    return None if x is None else float(x)


def leading_side(p_yes: float | None, p_no: float | None) -> int | None:
    """Outcome index of the leading side (ties -> YES); None when neither price is known."""
    if p_yes is None and p_no is None:
        return None
    if p_no is None:
        return 0 if p_yes >= 0.5 else 1
    if p_yes is None:
        return 1 if p_no > 0.5 else 0
    return 0 if p_yes >= p_no else 1


class Cherry(Strategy):
    family = "cherry"

    def p(self, sport=None):
        return {**DEFAULTS, **super().p(sport)}

    def in_scope(self, category: str, in_core: bool, prm: dict) -> bool:
        cats = prm.get("categories")
        if cats and category not in set(cats):
            return False
        if category in set(prm.get("exclude_categories") or []):
            return False
        return bool(prm.get("include_core_sports", True)) or not in_core

    def entry_signals(self, view: MarketView, now: int, ledger: Ledger) -> list[EntryIntent]:
        self.skips = []
        gv = getattr(view, "general", None)
        if gv is None:
            return []
        prm = self.p()
        traded = {p.condition_id for p in ledger.positions} | set(ledger.blocked_conditions)
        lo, hi = float(prm["entry_min"]), float(prm["entry_max"])
        h_min, h_max = float(prm["hours_to_end_min"]), float(prm["hours_to_end_max"])
        intents = []
        for gm in gv.candidates(now, h_min, h_max):
            if gm.condition_id in traded or not self.in_scope(gm.category, gm.in_core, prm):
                continue
            if gm.listed_at is not None and gm.end_ref - gm.listed_at < float(prm["min_listed_hours"]) * 3600 - EPS:
                continue
            vol, liq = gv.volume_asof(gm, now)
            if (vol or 0) + EPS < float(prm["min_volume"]) or (liq or 0) + EPS < float(prm["min_liquidity"] or 0):
                continue
            m = view.market(gm.condition_id)
            if m is None or not view.is_tradable(m, now) or len(m.tokens) < 2:
                continue
            age = int(prm["price_max_age_s"])
            py, pn = view.price(m.tokens[0].token_id, now, max_age_s=age), view.price(m.tokens[1].token_id, now,
                                                                                      max_age_s=age)
            idx = leading_side(py[1] if py else None, pn[1] if pn else None)
            if idx is None:
                continue
            quote = (py, pn)[idx]
            price = quote[1] if quote else (1.0 - (pn if idx == 0 else py)[1])
            if not (lo - EPS <= price <= hi + EPS):
                continue
            tok = m.tokens[idx]
            book = view.book(tok.token_id, now, max_age_s=int(prm["book_max_age_s"]))
            if book is None or book.best_ask is None:
                self.skip(gm.condition_id, "no_book")
                continue
            if book.best_ask > hi + EPS:
                self.skip(gm.condition_id, "ask_above_band")
                continue
            hours_left = (gm.end_ref - now) / 3600
            tx = _num(prm.get("time_exit_hours_before_end"))
            intents.append(EntryIntent(
                token_id=tok.token_id, condition_id=gm.condition_id, game_key=None, sport=gm.category,
                league=None, outcome_label=tok.outcome_label, signal_price=price, min_price=lo, max_price=hi,
                reason=f"{gm.category} lead {price:.3f} in [{lo}, {hi}] {hours_left:.1f}h to end",
                exit_rules={"v": RULES_VERSION,
                            "take_profit_delta": prm["take_profit_delta"],
                            "take_profit_price": prm["take_profit_price"],
                            "hold_above_price": prm["hold_above_price"],
                            "time_exit_at": int(gm.end_ref - tx * 3600) if tx is not None else None,
                            "end_ref": gm.end_ref,
                            "take_profit_percent": None, "stop_loss_percent": None, "trailing_enabled": False,
                            "submit_mid": book.mid},
                context_tokens=[tok.token_id],
                features={"mid": book.mid, "ask": book.best_ask, "hours_left": round(hours_left, 3),
                          "category": gm.category, "in_core": gm.in_core, "volume": vol, "liquidity": liq,
                          "neg_risk": gm.neg_risk, "event_id": gm.event_id},
                priority=(gm.end_ref, gm.condition_id)))
        intents.sort(key=lambda i: i.priority)
        return intents

    def confirm_entry(self, intent: EntryIntent, books: dict[str, Book], notional_usdc: float) -> Check:
        floor = _num(self.p(intent.sport).get("min_ask_depth_usd"))
        if floor is not None and floor > notional_usdc + EPS:
            depth = band_walk_check(books.get(intent.token_id), floor, intent.min_price, intent.max_price,
                                    self.min_order_shares)
            if not depth.ok:
                return Check(False, f"depth_floor:{depth.reason}", depth.walk_vwap, depth.limit_price)
        return super().confirm_entry(intent, books, notional_usdc)

    # ------------------------------------------------------------ exits
    @staticmethod
    def tp_threshold(position: PositionView) -> float | None:
        """Absolute TP bid-VWAP threshold from the frozen rules; None = off (legacy positions)."""
        r = position.exit_rules or {}
        cands = []
        if r.get("take_profit_delta") is not None and position.entry_price:
            cands.append(float(position.entry_price) + float(r["take_profit_delta"]))
        if r.get("take_profit_price") is not None:
            cands.append(float(r["take_profit_price"]))
        return round(min(cands), 6) if cands else None

    def decide(self, position: PositionView, price: float, hwm: float) -> tuple[str, str] | None:
        """Legacy midpoint chain: SL -> relative TP -> trailing. A null threshold is off."""
        r = position.exit_rules
        entry = position.entry_price
        if not entry:
            return None
        pnl = (price - entry) / entry
        sl = r.get("stop_loss_percent", -0.08)
        tp = r.get("take_profit_percent", 0.20)
        if sl is not None and pnl <= float(sl) + EPS:
            return "stop_loss", f"pnl {pnl:.3f}"
        if tp is not None and pnl >= float(tp) - EPS:
            return "take_profit", f"pnl {pnl:.3f}"
        if r.get("trailing_enabled", True) and price < hwm * (1 - float(r.get("trailing_percent", 0.15))) - EPS:
            return "trailing_stop", f"price {price:.3f} < hwm {hwm:.3f}"
        return None

    def exit_signals(self, view: MarketView, now: int, position: PositionView) -> ExitIntent | None:
        if position.status != "open" or (position.shares or 0) + EPS < self.min_order_shares:
            return None
        r = position.exit_rules or {}
        book = view.book(position.token_id, now, max_age_s=int(self.p()["book_max_age_s"]))
        if book is None or book.mid is None:
            return None
        hold = _num(r.get("hold_above_price"))
        if hold is not None and book.best_bid is not None and book.best_bid >= hold - EPS:
            return None
        tp = self.tp_threshold(position)
        sched = None
        if tp is not None and (hold is None or tp < hold - EPS):
            m = view.market(position.condition_id)
            sched = parse_fee_schedule(m.fee_schedule) if m else None
            c = net_positive_tp_check(position, book, tp, sched)
            if c.ok:
                return ExitIntent(position.position_id, "take_profit", c.shares, tp,
                                  f"full-holding bid vwap {c.walk_vwap:.4f} >= tp {tp} net-positive",
                                  {"vwap": c.walk_vwap, "tp": tp, "absolute_tp": True,
                                   "fee_schedule": sched.__dict__ if sched else None})
        if r.get("v") == RULES_VERSION:
            at = r.get("time_exit_at")
            if at is not None and now >= int(at) and book.best_bid is not None:
                floor = max(0.001, round(book.best_bid - TIME_EXIT_SLIPPAGE, 3))
                return ExitIntent(position.position_id, "time_exit", floor2(position.shares), floor,
                                  f"time exit {(int(r.get('end_ref') or at) - now) / 3600:.1f}h before end",
                                  {"bid": book.best_bid})
            return None
        price = book.mid
        bars = [p for _, p in view.price_bars(position.token_id, position.opened_at, now)]
        hwm = max([price, float(r.get("submit_mid") or 0), *bars])
        hit = self.decide(position, price, hwm)
        if hit is None:
            return None
        kind, why = hit
        floor = (position.entry_price or 0) if kind == "take_profit" else 0.001
        return ExitIntent(position.position_id, kind, floor2(position.shares), floor, why,
                          {"mid": price, "hwm": hwm})

    def confirm_exit(self, position: PositionView, intent: ExitIntent, book: Book | None) -> Check:
        r = position.exit_rules or {}
        hold = _num(r.get("hold_above_price"))
        if hold is not None and book is not None and book.best_bid is not None and book.best_bid >= hold - EPS:
            return Check(False, "hold_above_price")
        if intent.kind == "take_profit" and intent.features.get("absolute_tp"):
            if book is None or book.best_bid is None:
                return Check(False, "no_bids")
            tp = self.tp_threshold(position)
            if tp is None:
                return Check(False, "tp_disabled")
            raw = intent.features.get("fee_schedule")
            return net_positive_tp_check(position, book, tp, FeeSchedule(**raw) if raw else None)
        return super().confirm_exit(position, intent, book)
