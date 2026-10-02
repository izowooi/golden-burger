"""Watermelon: buy in-play near-certain winners (exact-$5 ask VWAP in [prob_min, prob_max]).
Exits, in priority order:
1. early take-profit (optional, `take_profit_delta`): sell the whole holding once its bid VWAP is
   >= min(entry_vwap + delta, take_profit_cap) and the sale is net-positive after buy+sell fees.
   Added 2026-10-02 because the 1-minute cadence cannot execute stops in late-game collapses
   (live stops filled 0.42-0.48 below stop_price); banking the edge early caps that exposure.
2. catastrophe stop on the displayed best bid (unchanged legacy logic);
otherwise hold to resolution.

Spec: docs/strategies/watermelon.md. Port notes:
- The Gamma/league classifier lives in the collector; here "eligible" = a live game whose
  whole-game result tokens are in core.db (soccer YES of home/draw/away; US team tokens).
- In-play age uses the scheduled start (no sports clock), as in the legacy strategy.
- Resolution is checked by the engine every cycle from core.db (legacy only checked it when
  the book vanished).
"""

from __future__ import annotations

from polylab.execution.fees import FeeSchedule, parse_fee_schedule
from polylab.marketview import EPS, Book, MarketView
from polylab.strategies.base import (
    game_in_scope, Check, EntryIntent, ExitIntent, Ledger, PositionView, Strategy,
                                     band_walk_check, floor2, net_positive_tp_check, result_tokens)

DEFAULTS = {
    "prob_min": 0.92,
    "prob_max": 0.999,
    "stop_price": 0.65,
    "max_entry_drawdown": 0.30,
    "use_stored_stop": False,
    "hours_max": 4.0,
    "baseline_usdc": 5.0,
    "max_stop_spread": 0.10,
    "reentry_cooldown_hours": 720,
    "book_max_age_s": 120,
    "take_profit_delta": None,          # None = hold to resolution (legacy behaviour)
    "take_profit_cap": 0.99,
}


def entry_stop_price(stop_floor: float, vwap: float, max_drawdown: float) -> float:
    return round(max(stop_floor, vwap - max_drawdown), 6)


class Watermelon(Strategy):
    family = "watermelon"

    def p(self, sport=None):
        return {**DEFAULTS, **super().p(sport)}

    # ------------------------------------------------------------ entries
    def _reentry_block(self, ledger: Ledger, game_key: str, token_id: str, now: int, cooldown_h: float) -> str | None:
        event_pos = [p for p in ledger.positions if p.game_key == game_key and p.status != "unfilled"]
        if any(p.is_open for p in event_pos):
            return "event_position_open"
        for p in event_pos:
            if p.token_id == token_id and (p.closed_at is None or now - p.closed_at < cooldown_h * 3600):
                return "token_cooldown"
        tokens = {p.token_id for p in event_pos}
        if len(tokens) >= 2:
            return "event_reversal_limit"
        if tokens and not any((p.exit_reason or "").startswith("stop_loss") for p in event_pos):
            return "event_already_traded"
        return None

    def entry_signals(self, view: MarketView, now: int, ledger: Ledger) -> list[EntryIntent]:
        self.skips = []
        intents: list[EntryIntent] = []
        for sport in self.sports:
            prm = self.p(sport)
            for game in view.live_games(now, [sport], max_age_hours=float(prm["hours_max"])):
                if not game_in_scope(view, game, prm):
                    continue
                tokens = result_tokens(view, game, include_no=False)
                if tokens is None:
                    self.skip(game.game_key, "result_set_incomplete")
                    continue
                in_band = []
                for rt in tokens:
                    if not view.is_tradable(rt.market, now):
                        continue
                    book = view.book(rt.token_id, now, max_age_s=int(prm["book_max_age_s"]))
                    if book is None or book.crossed:
                        continue
                    w = book.walk_buy(float(prm["baseline_usdc"]))
                    if w.ok and prm["prob_min"] - EPS <= w.vwap <= prm["prob_max"] + EPS:
                        in_band.append((rt, w))
                if not in_band:
                    continue
                if len(in_band) > 1:
                    self.skip(game.game_key, "multiple_results_in_band")
                    continue
                rt, w = in_band[0]
                block = self._reentry_block(ledger, game.game_key, rt.token_id, now, prm["reentry_cooldown_hours"])
                if block:
                    self.skip(rt.token_id, block)
                    continue
                in_play_h = (now - game.start_time) / 3600
                intents.append(EntryIntent(
                    token_id=rt.token_id, condition_id=rt.market.condition_id, game_key=game.game_key,
                    sport=sport, league=game.league, outcome_label=rt.label,
                    signal_price=w.vwap, min_price=prm["prob_min"], max_price=prm["prob_max"],
                    reason=f"baseline_vwap {w.vwap:.4f} in [{prm['prob_min']}, {prm['prob_max']}]",
                    exit_rules={
                        "stop_price": prm["stop_price"],
                        "max_entry_drawdown": prm["max_entry_drawdown"],
                        "use_stored_stop": bool(prm["use_stored_stop"]),
                        "stop_price_at_entry": entry_stop_price(prm["stop_price"], w.vwap, prm["max_entry_drawdown"]),
                        "max_stop_spread": prm["max_stop_spread"],
                        "take_profit_delta": prm["take_profit_delta"],
                        "take_profit_cap": prm["take_profit_cap"],
                    },
                    context_tokens=[rt.token_id],
                    features={"in_play_hours": round(in_play_h, 4), "baseline_vwap": w.vwap,
                              "limit_price": w.limit_price, "kind": rt.kind},
                    priority=(in_play_h, game.game_key, rt.market.condition_id)))
        intents.sort(key=lambda i: i.priority)
        return intents

    def confirm_entry(self, intent: EntryIntent, books: dict[str, Book], notional_usdc: float) -> Check:
        book = books.get(intent.token_id)
        base = band_walk_check(book, self.p(intent.sport)["baseline_usdc"], intent.min_price, intent.max_price, 0)
        if not base.ok:
            return Check(False, f"baseline_{base.reason}")
        return band_walk_check(book, notional_usdc, intent.min_price, intent.max_price, self.min_order_shares)

    # ------------------------------------------------------------ exits
    def stop_level(self, position: PositionView) -> float:
        r = position.exit_rules
        if r.get("use_stored_stop") and r.get("stop_price_at_entry") is not None:
            return float(r["stop_price_at_entry"])
        return entry_stop_price(float(r.get("stop_price", DEFAULTS["stop_price"])), float(position.entry_price or 0),
                                float(r.get("max_entry_drawdown", DEFAULTS["max_entry_drawdown"])))

    def tp_threshold(self, position: PositionView) -> float | None:
        """min(entry_vwap + delta, cap) from the rules frozen at entry; None = TP disabled."""
        r = position.exit_rules
        delta = r.get("take_profit_delta")
        if delta is None or position.entry_price is None:
            return None
        cap = float(r.get("take_profit_cap") if r.get("take_profit_cap") is not None else DEFAULTS["take_profit_cap"])
        return round(min(float(position.entry_price) + float(delta), cap), 6)

    def exit_signals(self, view: MarketView, now: int, position: PositionView) -> ExitIntent | None:
        if position.status != "open" or not position.shares:
            return None
        book = view.book(position.token_id, now, max_age_s=int(self.p(position.sport)["book_max_age_s"]))
        if book is None or book.best_bid is None:
            return None
        tp = self.tp_threshold(position)
        if tp is not None:
            m = view.market(position.condition_id)
            sched = parse_fee_schedule(m.fee_schedule) if m else None
            c = net_positive_tp_check(position, book, tp, sched)
            if c.ok:
                return ExitIntent(position.position_id, "take_profit", c.shares, tp,
                                  f"full-holding bid vwap {c.walk_vwap:.4f} >= tp {tp} net-positive",
                                  {"vwap": c.walk_vwap, "tp": tp, "fee_schedule": sched.__dict__ if sched else None})
            # any TP failure (depth, fee unknown, not net-positive) falls through to the stop
        stop = self.stop_level(position)
        if book.best_bid > stop:
            return None
        shares = floor2(position.shares)
        if shares <= 0:
            return None
        return ExitIntent(position.position_id, "stop_loss", shares, 0.001,
                          f"best_bid {book.best_bid} <= stop {stop}",
                          {"best_bid": book.best_bid, "stop": stop})

    def confirm_exit(self, position: PositionView, intent: ExitIntent, book: Book | None) -> Check:
        if intent.kind == "take_profit":
            if book is None or book.best_bid is None:
                return Check(False, "no_bids")
            tp = self.tp_threshold(position)
            if tp is None:
                return Check(False, "tp_disabled")
            sched_raw = intent.features.get("fee_schedule")
            return net_positive_tp_check(position, book, tp, FeeSchedule(**sched_raw) if sched_raw else None)
        if book is None or book.best_bid is None:
            return Check(False, "no_bids")
        stop = self.stop_level(position)
        if book.best_bid > stop:
            return Check(False, "recovered_above_stop")
        spread = book.spread
        max_spread = float(position.exit_rules.get("max_stop_spread", DEFAULTS["max_stop_spread"]))
        if spread is None or spread > max_spread + EPS:
            return Check(False, "stop_spread_too_wide")
        w = book.walk_sell(intent.shares)
        if not w.ok or not (0 < w.limit_price < 1):
            return Check(False, "insufficient_bid_depth")
        # gap stops are allowed: an executable, tight book is the only hard gate
        return Check(True, "ok", w.vwap, w.limit_price, w.shares)
