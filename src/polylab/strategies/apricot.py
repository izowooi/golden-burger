"""Apricot: late-game favourite (MLB, NBA, NHL; per-sport params in sport_overrides). At wall-clock
minute [tick, tick+window] after tick0 (first
minute both team books were executable while live), buy the unique midpoint leader if its
exact-$5 ask VWAP is in [prob_min, prob_max]. Exit at the first full-holding bid VWAP >= TP
that is net-positive after fees; otherwise hold to resolution.

Spec: docs/strategies/apricot.md. tick0 comes from the shared recorder's book snapshots
(MarketView.tick0), so it is no longer runtime-local and both arms share it.
"""

from __future__ import annotations

from polylab.execution.fees import FeeSchedule, parse_fee_schedule
from polylab.marketview import EPS, Book, MarketView
from polylab.strategies.base import (
    game_in_scope, Check, EntryIntent, ExitIntent, Ledger, PositionView, Strategy,
                                     band_walk_check, event_traded, net_positive_tp_check, result_tokens)

DEFAULTS = {
    "entry_tick_minute": 90,
    "tick_window_minutes": 2,
    "prob_min": 0.90,
    "prob_max": 0.999,
    "max_entry_spread": 0.05,
    "min_leader_margin": 0.005,
    "take_profit_price": 0.90,
    "max_exit_spread": 0.10,
    "hours_max": 8.0,
    "baseline_usdc": 5.0,
    "book_max_age_s": 120,
    # Opt-in sport clock (2026-10-05). None = wall-clock minutes since tick0 (the only clock the
    # NBA/NHL/MLB history can replay, so every validated setting uses it). When set, a fresh live
    # game state's elapsed game minute (NBA 0-48, NHL 0-60 + OT, MLB unsupported) decides the window
    # [entry_game_minute, +tick_window_minutes]; without a fresh state it falls back to the wall tick.
    "entry_game_minute": None,
    "game_state_max_age_s": 600,
}


def pick_leader(books: dict[str, Book], labels: dict[str, str]) -> tuple[str, float] | None:
    """(leader token, margin) by highest midpoint; ties broken by label."""
    mids = sorted(((-books[t].mid, labels[t], t) for t in books if books[t].mid is not None))
    if len(mids) != len(books) or len(mids) < 2:
        return None
    return mids[0][2], (-mids[0][0]) - (-mids[1][0])


class Apricot(Strategy):
    family = "apricot"

    def p(self, sport=None):
        return {**DEFAULTS, **super().p(sport)}

    @staticmethod
    def _game_minute(view: MarketView, game_key: str, now: int, prm: dict) -> float | None:
        """Elapsed game minute from a fresh live game state, only when `entry_game_minute` is set."""
        if prm.get("entry_game_minute") is None:
            return None
        st = view.game_state(game_key, now)
        if st is None or st.game_minute is None or now - st.ts > int(prm["game_state_max_age_s"]) or st.ended:
            return None
        if st.live is False or (st.status or "").lower() in ("scheduled", "ended", "cancelled"):
            return None
        return float(st.game_minute)

    def _event_books(self, view: MarketView, now: int, token_ids: list[str], max_age: int) -> dict[str, Book] | None:
        books = {}
        for t in token_ids:
            b = view.book(t, now, max_age_s=max_age)
            if b is None or b.best_bid is None or b.crossed:
                return None
            books[t] = b
        return books

    def _signal(self, books: dict[str, Book], labels: dict[str, str], prm: dict) -> tuple[str, str, dict]:
        """Returns (status, leader_token, features); status 'ok' or a skip reason."""
        for t, b in books.items():
            if not b.walk_buy(prm["baseline_usdc"]).ok:
                return "incomplete_book", "", {}
            if b.spread is None or b.spread > prm["max_entry_spread"] + EPS:
                return "spread_too_wide", "", {}
        lead = pick_leader(books, labels)
        if lead is None:
            return "no_leader", "", {}
        leader, margin = lead
        if margin + EPS < prm["min_leader_margin"]:
            return "leader_margin_too_small", leader, {}
        w = books[leader].walk_buy(prm["baseline_usdc"])
        if not (prm["prob_min"] - EPS <= w.vwap <= prm["prob_max"] + EPS):
            return "leader_outside_band", leader, {"vwap": w.vwap}
        return "ok", leader, {"vwap": w.vwap, "margin": margin, "mid": books[leader].mid}

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
                tokens = result_tokens(view, game)
                if tokens is None or len(tokens) != 2:
                    self.skip(game.game_key, "result_set_incomplete")
                    continue
                if not view.is_tradable(tokens[0].market, now):
                    continue
                ids = [rt.token_id for rt in tokens]
                tick0 = view.tick0(game, ids, now, min_depth_usd=prm["baseline_usdc"])
                if tick0 is None:
                    continue
                minute = (now - tick0) / 60.0
                clock = "wall"
                tick = float(prm["entry_tick_minute"])
                gm = self._game_minute(view, game.game_key, now, prm)
                if gm is not None:
                    minute, tick, clock = gm, float(prm["entry_game_minute"]), "game"
                if not (tick - EPS <= minute <= tick + float(prm["tick_window_minutes"]) + EPS):
                    continue
                books = self._event_books(view, now, ids, int(prm["book_max_age_s"]))
                if books is None:
                    self.skip(game.game_key, "incomplete_book")
                    continue
                labels = {rt.token_id: rt.kind for rt in tokens}
                status, leader, feat = self._signal(books, labels, prm)
                if status != "ok":
                    self.skip(game.game_key, status)
                    continue
                rt = next(r for r in tokens if r.token_id == leader)
                intents.append(EntryIntent(
                    token_id=leader, condition_id=rt.market.condition_id, game_key=game.game_key, sport=sport,
                    league=game.league, outcome_label=rt.token.outcome_label or rt.kind,
                    signal_price=feat["vwap"], min_price=prm["prob_min"], max_price=prm["prob_max"],
                    reason=f"tick {minute:.1f}m ({clock}) leader vwap {feat['vwap']:.4f}",
                    exit_rules={"take_profit_price": prm["take_profit_price"],
                                "max_exit_spread": prm["max_exit_spread"]},
                    context_tokens=ids, game_minute=minute,
                    features={"source_minute": round(minute, 3), "clock": clock, "tick0": tick0, **feat, "kind": rt.kind,
                              "labels": labels},
                    priority=(minute, game.game_key)))
        intents.sort(key=lambda i: i.priority)
        return intents

    def confirm_entry(self, intent: EntryIntent, books: dict[str, Book], notional_usdc: float) -> Check:
        prm = self.p(intent.sport)
        ctx = {t: books.get(t) for t in intent.context_tokens}
        if any(b is None or b.best_bid is None or b.crossed for b in ctx.values()):
            return Check(False, "fresh_book_missing")
        status, leader, _ = self._signal(ctx, intent.features.get("labels") or {t: t for t in ctx}, prm)
        if status != "ok":
            return Check(False, f"fresh_{status}")
        if leader != intent.token_id:
            return Check(False, "leader_changed")
        return band_walk_check(ctx[leader], notional_usdc, prm["prob_min"], prm["prob_max"], self.min_order_shares)

    # ------------------------------------------------------------ exits
    def _tp_check(self, position: PositionView, book: Book, fee_schedule) -> Check:
        tp = float(position.exit_rules.get("take_profit_price", DEFAULTS["take_profit_price"]))
        return net_positive_tp_check(position, book, tp, fee_schedule)

    def exit_signals(self, view: MarketView, now: int, position: PositionView) -> ExitIntent | None:
        if position.status != "open":
            return None
        book = view.book(position.token_id, now, max_age_s=int(self.p(position.sport)["book_max_age_s"]))
        if book is None:
            return None
        m = view.market(position.condition_id)
        sched = parse_fee_schedule(m.fee_schedule) if m else None
        c = self._tp_check(position, book, sched)
        if not c.ok:
            return None
        return ExitIntent(position.position_id, "take_profit", c.shares,
                          float(position.exit_rules.get("take_profit_price", DEFAULTS["take_profit_price"])),
                          f"full-holding bid vwap {c.walk_vwap:.4f} net-positive",
                          {"vwap": c.walk_vwap, "fee_schedule": sched.__dict__ if sched else None})

    def confirm_exit(self, position: PositionView, intent: ExitIntent, book: Book | None) -> Check:
        if book is None or book.best_bid is None:
            return Check(False, "no_bids")
        spread = book.spread
        if spread is None or spread > float(position.exit_rules.get("max_exit_spread", 0.10)) + EPS:
            return Check(False, "exit_spread_too_wide")
        sched_raw = intent.features.get("fee_schedule")
        sched = FeeSchedule(**sched_raw) if sched_raw else None
        return self._tp_check(position, book, sched)
