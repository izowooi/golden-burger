"""Cherry (resolution momentum), restricted to the 5-sport game-outcome universe.

Buy the index-0 (YES) outcome when its price is in [buy_threshold, sell_threshold]
(both inclusive) within (entry_hours_min, entry_hours_max] before the game or in-play
(optionally only the first `in_play_max_minutes`). One entry per condition forever; a midpoint
above the band at order time (`rapid_jump`) permanently excludes the market.

Exits (rules frozen into exit_rules at entry; checked in this order):
1. hold_above_price (optional): best bid at/above it -> no exit at all, ride to resolution.
2. absolute take-profit (optional, added 2026-10-04): sell the whole holding once its bid VWAP is
   >= min(entry_vwap + take_profit_delta, take_profit_price) (whichever are set) and the sale is
   net-positive after buy+sell fees (base.net_positive_tp_check; unknown fee never passes).
3. legacy midpoint chain SL -> relative TP -> trailing (first hit wins); stop_loss_percent and
   take_profit_percent may be null (= off), trailing via trailing_enabled.
Positions opened before 2026-10-04 carry no absolute-TP keys and keep the legacy chain unchanged.

Spec: docs/strategies/cherry.md. Deliberate deviations:
- Legacy rested GTC orders at the midpoint (maker, no fee). polylab executes FOK only, so entries
  take the ask (walk capped at sell_threshold, taker fee) and exits hit the bid.
- Legacy swept every Gamma category; this port only sees the 5 sports in core.db.
- The high-water mark is the max canonical price since entry (no mutable state).
- min_ask_depth_usd (optional): the fresh ask walk for max(stake, this USD) must stay inside the
  band — a book-depth liquidity floor on top of Gamma `liquidity`.
"""

from __future__ import annotations

from polylab.execution.fees import FeeSchedule, parse_fee_schedule
from polylab.marketview import EPS, Book, MarketView
from polylab.strategies.base import (Check, EntryIntent, ExitIntent, Ledger, PositionView, Strategy, band_walk_check,
                                     floor2, game_in_scope, net_positive_tp_check)

DEFAULTS = {
    "buy_threshold": 0.80,
    "sell_threshold": 0.82,
    "take_profit_percent": 0.20,        # relative TP on the midpoint; None = off
    "stop_loss_percent": -0.08,         # relative SL on the midpoint; None = off
    "trailing_enabled": True,
    "trailing_percent": 0.15,
    "take_profit_delta": None,          # absolute TP: bid VWAP >= entry + delta; None = off
    "take_profit_price": None,          # absolute TP at a fixed bid VWAP (e.g. 0.98); None = off
    "hold_above_price": None,           # e.g. 0.99: best bid at/above -> hold to resolution
    "min_ask_depth_usd": None,          # book-depth floor inside the band; None = stake only
    "entry_hours_min": 0.0,
    "entry_hours_max": 120.0,
    "allow_in_play": True,
    "in_play_max_minutes": None,        # None = sport default in-play age
    "yes_only": True,
    "min_liquidity": 125000.0,
    "min_volume": 5000.0,
    "market_types": ["moneyline", "draw"],
    "price_max_age_s": 900,
    "book_max_age_s": 300,
}


def _num(x) -> float | None:
    return None if x is None else float(x)


class Cherry(Strategy):
    family = "cherry"

    def p(self, sport=None):
        return {**DEFAULTS, **super().p(sport)}

    def entry_signals(self, view: MarketView, now: int, ledger: Ledger) -> list[EntryIntent]:
        self.skips = []
        intents = []
        traded = {p.condition_id for p in ledger.positions} | set(ledger.blocked_conditions)
        for sport in self.sports:
            prm = self.p(sport)
            games = list(view.upcoming_games(now, float(prm["entry_hours_max"]), [sport]))
            if prm["allow_in_play"]:
                ip = prm.get("in_play_max_minutes")
                live = (view.live_games(now, [sport]) if ip is None
                        else view.live_games(now, [sport], max_age_hours=float(ip) / 60.0))
                games = live + games
            for game in games:
                if not game_in_scope(view, game, prm):
                    continue
                hours_left = (game.start_time - now) / 3600
                in_play = hours_left <= 0
                if not in_play and not (prm["entry_hours_min"] - EPS <= hours_left <= prm["entry_hours_max"] + EPS):
                    continue
                for m in view.markets(game.game_key, types=prm["market_types"]):
                    if m.condition_id in traded or not view.is_tradable(m, now) or len(m.tokens) < 2:
                        continue
                    if (m.liquidity or 0) + EPS < prm["min_liquidity"] or (m.volume or 0) + EPS < prm["min_volume"]:
                        continue
                    prices = [view.price(t.token_id, now, max_age_s=int(prm["price_max_age_s"])) for t in m.tokens[:2]]
                    if prm["yes_only"]:
                        idx = 0
                    else:
                        if prices[0] is None or prices[1] is None:
                            continue
                        idx = 0 if prices[0][1] >= prices[1][1] else 1
                    if prices[idx] is None:
                        continue
                    price = prices[idx][1]
                    lo, hi = float(prm["buy_threshold"]), float(prm["sell_threshold"])
                    if not (lo - EPS <= price <= hi + EPS):
                        continue
                    tok = m.tokens[idx]
                    book = view.book(tok.token_id, now, max_age_s=int(prm["book_max_age_s"]))
                    mid = book.mid if book else None
                    if mid is None:
                        self.skip(m.condition_id, "midpoint_unavailable")
                        continue
                    if mid > hi + EPS:
                        self.skip(m.condition_id, "rapid_jump")
                        continue
                    if mid < lo - EPS:
                        self.skip(m.condition_id, "midpoint_below_band")
                        continue
                    intents.append(EntryIntent(
                        token_id=tok.token_id, condition_id=m.condition_id, game_key=game.game_key, sport=sport,
                        league=game.league, outcome_label=tok.outcome_label,
                        signal_price=price, min_price=lo, max_price=hi,
                        reason=f"price {price:.3f} in [{lo}, {hi}] {'in-play' if in_play else f'{hours_left:.1f}h pre'}",
                        exit_rules={"take_profit_percent": prm["take_profit_percent"],
                                    "stop_loss_percent": prm["stop_loss_percent"],
                                    "trailing_enabled": bool(prm["trailing_enabled"]),
                                    "trailing_percent": prm["trailing_percent"],
                                    "take_profit_delta": prm["take_profit_delta"],
                                    "take_profit_price": prm["take_profit_price"],
                                    "hold_above_price": prm["hold_above_price"],
                                    "submit_mid": mid},
                        context_tokens=[tok.token_id],
                        features={"mid": mid, "hours_left": round(hours_left, 3), "market_type": m.market_type,
                                  "liquidity": m.liquidity, "in_play": in_play},
                        priority=(game.start_time or 0, m.condition_id)))
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
        sl = r.get("stop_loss_percent", DEFAULTS["stop_loss_percent"])
        tp = r.get("take_profit_percent", DEFAULTS["take_profit_percent"])
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
        if tp is not None and (hold is None or tp < hold - EPS):
            m = view.market(position.condition_id)
            sched = parse_fee_schedule(m.fee_schedule) if m else None
            c = net_positive_tp_check(position, book, tp, sched)
            if c.ok:
                return ExitIntent(position.position_id, "take_profit", c.shares, tp,
                                  f"full-holding bid vwap {c.walk_vwap:.4f} >= tp {tp} net-positive",
                                  {"vwap": c.walk_vwap, "tp": tp, "absolute_tp": True,
                                   "fee_schedule": sched.__dict__ if sched else None})
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
