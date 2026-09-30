"""Cherry (resolution momentum), restricted to the 5-sport game-outcome universe.

Buy the index-0 (YES) outcome when its price is in [buy_threshold, sell_threshold]
(both inclusive) within (0, entry_hours_max] before the game or in-play; exit on the
midpoint with SL -> TP -> trailing (first hit wins). One entry per condition forever; a
midpoint above the band at order time (`rapid_jump`) permanently excludes the market.

Spec: docs/strategies/cherry.md. Deliberate deviations:
- Legacy rested GTC orders at the midpoint. polylab executes FOK only, so entries take the
  ask (walk capped at sell_threshold) and exits hit the bid. Expect worse fills than legacy.
- Legacy swept every Gamma category; this port only sees the 5 sports in core.db.
- The high-water mark is the max canonical price since entry (no mutable state).
"""

from __future__ import annotations

from polylab.marketview import EPS, Book, MarketView
from polylab.strategies.base import Check, EntryIntent, ExitIntent, Ledger, PositionView, Strategy, floor2

DEFAULTS = {
    "buy_threshold": 0.80,
    "sell_threshold": 0.82,
    "take_profit_percent": 0.20,
    "stop_loss_percent": -0.08,
    "trailing_enabled": True,
    "trailing_percent": 0.15,
    "entry_hours_min": 0.0,
    "entry_hours_max": 120.0,
    "allow_in_play": True,
    "yes_only": True,
    "min_liquidity": 125000.0,
    "min_volume": 5000.0,
    "market_types": ["moneyline", "draw"],
    "price_max_age_s": 900,
    "book_max_age_s": 300,
}


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
                games = view.live_games(now, [sport]) + games
            for game in games:
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
                                    "submit_mid": mid},
                        context_tokens=[tok.token_id],
                        features={"mid": mid, "hours_left": round(hours_left, 3), "market_type": m.market_type,
                                  "liquidity": m.liquidity},
                        priority=(game.start_time or 0, m.condition_id)))
        intents.sort(key=lambda i: i.priority)
        return intents

    # ------------------------------------------------------------ exits
    def _mark(self, view: MarketView, now: int, token_id: str) -> float | None:
        book = view.book(token_id, now, max_age_s=int(self.p()["book_max_age_s"]))
        if book is not None and book.mid is not None:
            return book.mid
        return None

    def decide(self, position: PositionView, price: float, hwm: float) -> tuple[str, str] | None:
        r = position.exit_rules
        entry = position.entry_price
        if not entry:
            return None
        pnl = (price - entry) / entry
        if pnl <= float(r.get("stop_loss_percent", DEFAULTS["stop_loss_percent"])) + EPS:
            return "stop_loss", f"pnl {pnl:.3f}"
        if pnl >= float(r.get("take_profit_percent", DEFAULTS["take_profit_percent"])) - EPS:
            return "take_profit", f"pnl {pnl:.3f}"
        if r.get("trailing_enabled", True) and price < hwm * (1 - float(r.get("trailing_percent", 0.15))) - EPS:
            return "trailing_stop", f"price {price:.3f} < hwm {hwm:.3f}"
        return None

    def exit_signals(self, view: MarketView, now: int, position: PositionView) -> ExitIntent | None:
        if position.status != "open" or (position.shares or 0) + EPS < self.min_order_shares:
            return None
        price = self._mark(view, now, position.token_id)
        if price is None:
            return None
        bars = [p for _, p in view.price_bars(position.token_id, position.opened_at, now)]
        hwm = max([price, float(position.exit_rules.get("submit_mid") or 0), *bars])
        hit = self.decide(position, price, hwm)
        if hit is None:
            return None
        kind, why = hit
        floor = (position.entry_price or 0) if kind == "take_profit" else 0.001
        return ExitIntent(position.position_id, kind, floor2(position.shares), floor, why,
                          {"mid": price, "hwm": hwm})
