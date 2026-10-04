"""Plum: buy the unique in-play midpoint leader (soccer: across 6 YES/NO books, US: 2 team
books) when its exact-$5 ask VWAP is in a narrow band (.70-.73). Exits: absolute TP
(partial slice allowed, every consumed bid >= TP), stop at best bid <= entry - delta, and
(soccer) a forced full exit at source minute >= force_exit_minute. Otherwise hold.

Early take-profit (optional, added 2026-10-04 by owner decision B): with `take_profit_delta` set,
the TP becomes a full-holding sale once the bid VWAP of the whole holding is
>= min(entry_vwap + delta, take_profit_price) and the sale is net-positive after buy+sell fees
(base.net_positive_tp_check; unknown fee never passes). It replaces the partial-slice TP.
`hold_above_price` (optional): best bid at/above it -> no exit at all (TP, stop or forced), ride
to resolution. Positions without these keys keep the legacy rules.

Spec: docs/strategies/plum.md. Rules are frozen per position at entry (exit_rules).
"""

from __future__ import annotations

import re

from polylab.execution.fees import FeeSchedule, parse_fee_schedule
from polylab.marketview import EPS, Book, GameState, MarketView
from polylab.strategies.apricot import pick_leader
from polylab.strategies.base import (
    game_in_scope, Check, EntryIntent, ExitIntent, Ledger, PositionView, Strategy,
                                     band_walk_check, event_traded, floor2, net_positive_tp_check, result_tokens)

DEFAULTS = {
    "prob_min": 0.70,
    "prob_max": 0.73,
    "take_profit_price": 0.90,
    "take_profit_delta": None,       # absolute early TP over the entry VWAP (full holding, net of fees)
    "hold_above_price": None,        # e.g. 0.99: best bid at/above -> hold to resolution
    "stop_loss_delta": 0.12,
    "max_source_minute": None,       # soccer override: 60
    "force_exit_minute": None,       # soccer override: 65
    "min_leader_margin": 0.005,
    "max_entry_spread": 0.05,
    "max_stop_spread": 0.10,
    "hours_max": 12.0,               # legacy had no age cap; bound stale "live" rows
    "baseline_usdc": 5.0,
    "book_max_age_s": 120,
    "state_max_age_s": 1200,
}

_CLOCK = re.compile(r"^(?:(\d+):)?(\d+)(?::(\d+))?$")


def _elapsed_minutes(elapsed: str | None) -> float | None:
    if elapsed is None:
        return None
    t = str(elapsed).strip().lower().rstrip("'").removesuffix("min").strip().rstrip("'")
    if not t:
        return None
    if "+" in t:
        base, _, extra = t.partition("+")
        try:
            return float(base) + float(extra)
        except ValueError:
            return None
    try:
        return float(t)
    except ValueError:
        pass
    m = _CLOCK.match(t)
    if not m:
        return None
    h, mm, ss = m.group(1), m.group(2), m.group(3)
    if ss is None:           # "MM:SS"
        return float(h or 0) + float(mm) / 60 if h is not None else float(mm)
    return float(h or 0) * 60 + float(mm) + float(ss) / 60


def soccer_regulation_minute(elapsed: str | None, period: str | None) -> float | None:
    """Legacy get_source_regulation_minute: decode elapsed by period; unsupported -> None."""
    per = (period or "").strip().casefold()
    if per in ("ht", "half time", "halftime"):
        return 45.0
    if per in ("ft", "full time", "fulltime"):
        return 90.0
    e = _elapsed_minutes(elapsed)
    if e is None:
        return None
    if per in ("1h", "first half", "1", "first"):
        return e
    if per in ("2h", "second half", "2", "second"):
        return 45 + e if e < 45 else e
    return None


def source_minute(state: GameState | None, now: int, max_age_s: int) -> float | None:
    """Current regulation minute. The feed only emits on change, so a running clock
    (1H/2H) is advanced by the state's age; stale states give None (never wall clock)."""
    if state is None or now - state.ts > max_age_s:
        return None
    minute = soccer_regulation_minute(state.elapsed, state.period)
    if minute is None:
        return None if state.game_minute is None else float(state.game_minute)
    if (state.period or "").strip().casefold() in ("1h", "first half", "1", "first", "2h", "second half", "2", "second"):
        minute += max(0, now - state.ts) / 60.0
    return minute


def tp_slice(bids: list[tuple[float, float]], position_shares: float, tp: float, min_shares: float = 5.0) -> float:
    """Shares sellable at >= TP without leaving a sub-minimum remainder (0 = no TP)."""
    profitable = floor2(min(position_shares, sum(s for p, s in bids if p + EPS >= tp)))
    if profitable + EPS < min_shares:
        return 0.0
    if profitable + EPS >= floor2(position_shares):
        return floor2(position_shares)
    slice_ = floor2(min(profitable, floor2(position_shares - min_shares)))
    return slice_ if slice_ + EPS >= min_shares else 0.0


class Plum(Strategy):
    family = "plum"

    def p(self, sport=None):
        return {**DEFAULTS, **super().p(sport)}

    def _signal(self, books: dict[str, Book], labels: dict[str, str], prm: dict) -> tuple[str, str, dict]:
        for b in books.values():
            if not b.bids or not b.walk_buy(prm["baseline_usdc"]).ok:
                return "incomplete_direct_book_coverage", "", {}
        lead = pick_leader(books, labels)
        if lead is None:
            return "no_leader", "", {}
        leader, margin = lead
        if margin + EPS < prm["min_leader_margin"]:
            return "leader_margin_too_small", leader, {}
        b = books[leader]
        if b.spread is None or b.spread > prm["max_entry_spread"] + EPS:
            return "leader_spread_too_wide", leader, {}
        w = b.walk_buy(prm["baseline_usdc"])
        if not (prm["prob_min"] - EPS <= w.vwap <= prm["prob_max"] + EPS):
            return "leader_outside_band", leader, {"vwap": w.vwap}
        return "ok", leader, {"vwap": w.vwap, "margin": margin, "mid": b.mid}

    def entry_signals(self, view: MarketView, now: int, ledger: Ledger) -> list[EntryIntent]:
        self.skips = []
        intents = []
        for sport in self.sports:
            prm = self.p(sport)
            for game in view.live_games(now, [sport], max_age_hours=float(prm["hours_max"])):
                if not game_in_scope(view, game, prm):
                    continue
                if event_traded(ledger, game.game_key):
                    continue
                minute = None
                if sport == "soccer":
                    minute = source_minute(view.game_state(game.game_key, now), now, int(prm["state_max_age_s"]))
                    if minute is None or minute < 0:
                        self.skip(game.game_key, "source_clock_required")
                        continue
                if prm["max_source_minute"] is not None and minute is not None \
                        and minute > float(prm["max_source_minute"]) + EPS:
                    continue
                tokens = result_tokens(view, game, include_no=True)
                expected = 6 if sport == "soccer" else 2
                if tokens is None or len(tokens) != expected:
                    self.skip(game.game_key, "result_set_incomplete")
                    continue
                if any(not view.is_tradable(rt.market, now) for rt in tokens):
                    continue
                books = {}
                for rt in tokens:
                    b = view.book(rt.token_id, now, max_age_s=int(prm["book_max_age_s"]))
                    if b is None or b.crossed:
                        break
                    books[rt.token_id] = b
                if len(books) != expected:
                    self.skip(game.game_key, "incomplete_direct_book_coverage")
                    continue
                labels = {rt.token_id: rt.label for rt in tokens}
                status, leader, feat = self._signal(books, labels, prm)
                if status != "ok":
                    self.skip(game.game_key, status)
                    continue
                rt = next(r for r in tokens if r.token_id == leader)
                intents.append(EntryIntent(
                    token_id=leader, condition_id=rt.market.condition_id, game_key=game.game_key, sport=sport,
                    league=game.league, outcome_label=rt.label if sport == "soccer" else (rt.token.outcome_label or rt.kind),
                    signal_price=feat["vwap"], min_price=prm["prob_min"], max_price=prm["prob_max"],
                    reason=f"leader {rt.label} vwap {feat['vwap']:.4f} margin {feat['margin']:.3f}",
                    exit_rules={
                        "take_profit_price": prm["take_profit_price"],
                        "take_profit_delta": prm["take_profit_delta"],
                        "hold_above_price": prm["hold_above_price"],
                        "stop_loss_delta": prm["stop_loss_delta"],
                        "force_exit_minute": prm["force_exit_minute"],
                        "stop_price_at_entry": round(max(0.01, feat["vwap"] - prm["stop_loss_delta"]), 6),
                        "max_stop_spread": prm["max_stop_spread"],
                        "state_max_age_s": prm["state_max_age_s"],
                    },
                    context_tokens=list(books), game_minute=minute,
                    features={**feat, "labels": labels, "source_minute": minute},
                    priority=(game.start_time or 0, game.game_key)))
        intents.sort(key=lambda i: i.priority)
        return intents

    def confirm_entry(self, intent: EntryIntent, books: dict[str, Book], notional_usdc: float) -> Check:
        prm = self.p(intent.sport)
        ctx = {t: books.get(t) for t in intent.context_tokens}
        if any(b is None or b.crossed for b in ctx.values()):
            return Check(False, "fresh_book_missing")
        status, leader, _ = self._signal(ctx, intent.features.get("labels") or {t: t for t in ctx}, prm)
        if status != "ok":
            return Check(False, f"fresh_{status}")
        if leader != intent.token_id:
            return Check(False, "leader_changed")
        return band_walk_check(ctx[leader], notional_usdc, prm["prob_min"], prm["prob_max"], self.min_order_shares)

    # ------------------------------------------------------------ exits
    @staticmethod
    def delta_tp(position: PositionView) -> float | None:
        """Early-TP threshold min(entry + take_profit_delta, take_profit_price); None = legacy TP."""
        r = position.exit_rules or {}
        if r.get("take_profit_delta") is None or not position.entry_price:
            return None
        tp = float(position.entry_price) + float(r["take_profit_delta"])
        if r.get("take_profit_price") is not None:
            tp = min(tp, float(r["take_profit_price"]))
        return round(tp, 6)

    def _plan(self, position: PositionView, book: Book, minute: float | None,
              fee_schedule: FeeSchedule | None = None) -> ExitIntent | None:
        r = position.exit_rules
        shares = floor2(position.shares or 0)
        if shares <= 0 or book.best_bid is None:
            return None
        hold = r.get("hold_above_price")
        if hold is not None and book.best_bid >= float(hold) - EPS:
            return None
        entry = position.entry_price or 0.0
        trigger = max(0.01, entry - float(r.get("stop_loss_delta", DEFAULTS["stop_loss_delta"])))
        tp = float(r.get("take_profit_price", DEFAULTS["take_profit_price"]))
        early = self.delta_tp(position)
        force = r.get("force_exit_minute")
        due = force is not None and minute is not None and minute >= float(force) - EPS
        feats = {"best_bid": book.best_bid, "trigger": trigger, "minute": minute}
        if not due:
            if early is not None:
                c = net_positive_tp_check(position, book, early, fee_schedule)
                if c.ok:
                    return ExitIntent(position.position_id, "take_profit", c.shares, early,
                                      f"full-holding bid vwap {c.walk_vwap:.4f} >= entry+delta {early} net-positive",
                                      {**feats, "vwap": c.walk_vwap, "tp": early, "delta_tp": True,
                                       "fee_schedule": fee_schedule.__dict__ if fee_schedule else None})
            else:
                n = tp_slice(book.bids, position.shares or 0, tp, self.min_order_shares)
                if n > 0:
                    return ExitIntent(position.position_id, "take_profit", n, tp, f"{n} shares bid >= {tp}", feats)
            if book.best_bid <= trigger + EPS:
                return ExitIntent(position.position_id, "stop_loss", shares, 0.001,
                                  f"best_bid {book.best_bid} <= trigger {trigger:.4f}", feats)
            return None
        kind = "stop_loss" if book.best_bid <= trigger + EPS else "time_exit"
        return ExitIntent(position.position_id, kind, shares, 0.001, f"minute {minute:.1f} >= {force}", feats)

    def exit_signals(self, view: MarketView, now: int, position: PositionView) -> ExitIntent | None:
        if position.status != "open":
            return None
        book = view.book(position.token_id, now, max_age_s=int(self.p(position.sport)["book_max_age_s"]))
        if book is None:
            return None
        minute = None
        if position.exit_rules.get("force_exit_minute") is not None and position.game_key:
            minute = source_minute(view.game_state(position.game_key, now), now,
                                   int(position.exit_rules.get("state_max_age_s", DEFAULTS["state_max_age_s"])))
        sched = None
        if self.delta_tp(position) is not None:
            m = view.market(position.condition_id)
            sched = parse_fee_schedule(m.fee_schedule) if m else None
        return self._plan(position, book, minute, sched)

    def confirm_exit(self, position: PositionView, intent: ExitIntent, book: Book | None) -> Check:
        if book is None or book.best_bid is None:
            return Check(False, "no_bids")
        hold = (position.exit_rules or {}).get("hold_above_price")
        if hold is not None and book.best_bid >= float(hold) - EPS:
            return Check(False, "hold_above_price")
        if intent.kind == "take_profit" and intent.features.get("delta_tp"):
            tp = self.delta_tp(position)
            if tp is None:
                return Check(False, "tp_disabled")
            raw = intent.features.get("fee_schedule")
            return net_positive_tp_check(position, book, tp, FeeSchedule(**raw) if raw else None)
        spread = book.spread
        if spread is None or spread > float(position.exit_rules.get("max_stop_spread", 0.10)) + EPS:
            return Check(False, "exit_spread_too_wide")
        if intent.kind == "take_profit":
            n = tp_slice(book.bids, position.shares or 0, intent.min_price, self.min_order_shares)
            if n <= 0:
                return Check(False, "tp_depth_gone")
            w = book.walk_sell(n)
            if not w.ok or w.limit_price + EPS < intent.min_price:
                return Check(False, "tp_depth_gone")
            return Check(True, "ok", w.vwap, w.limit_price, n)
        if intent.kind == "stop_loss" and book.best_bid > float(intent.features.get("trigger", 0)) + EPS:
            return Check(False, "recovered_above_stop")
        w = book.walk_sell(intent.shares)
        if not w.ok or not (0 < w.limit_price < 1):
            return Check(False, "full_stop_displayed_depth_unavailable")
        return Check(True, "ok", w.vwap, w.limit_price, w.shares)
