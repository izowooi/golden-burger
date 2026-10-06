"""Late leader (hypothesis "late-leader convergence", owner feedback 2026-10-06 evening; research note
docs/research/hypothesis-late-leader-convergence.md). Two-outcome moneylines only (NBA/NHL/NFL/MLB, no draws).

Entry, at most once per game: at wall-clock minute >= `min_wall_minute` (T) after the scheduled start (and <=
`max_wall_minute` when set) the midpoint leader's mid is >= `trigger_price` (Y) after having been below Y at some
canonical 1-minute bar since T (the crossing; `require_cross: false` drops it), and its exact-$5 ask VWAP is in
[Y - 0.02, Y + `max_entry_premium`]. Pre-game filter (`pregame_max`, X): the closing price (last canonical bar in the
`pregame_window_minutes` before the scheduled start) must be <= X for the higher of the two teams (`pregame_scope:
game`) or for the bought team (`token`); a game without a closing price is skipped while X is set.
Exits (frozen at entry, shared with apricot): full-holding take-profit at bid VWAP >= `take_profit_price` (Z) when
net-positive after fees (None = hold to resolution), optional stop at best bid <= entry VWAP - `stop_loss_delta`.

Clock: wall minutes since the scheduled start — the only clock the 2024-2026 history can replay (no historical game
states); the fast evaluator of the research note uses the same rules on the same canonical bars.
"""

from __future__ import annotations

from polylab.marketview import EPS, Book, MarketView
from polylab.strategies.apricot import Apricot, pick_leader
from polylab.strategies.base import (Check, EntryIntent, Ledger, PositionView, band_walk_check, event_traded,
                                     game_in_scope, net_positive_tp_check, result_tokens)

DEFAULTS = {
    "min_wall_minute": 0,                # T
    "max_wall_minute": None,
    "trigger_price": 0.80,               # Y
    "max_entry_premium": 0.03,           # ask VWAP <= Y + premium (no chasing a jump straight to 0.97)
    "entry_floor_slack": 0.02,           # ask VWAP >= Y - slack (fresh-book wobble)
    "require_cross": True,
    "pregame_max": None,                 # X; None = no pre-game filter
    "pregame_scope": "game",             # game | token
    "pregame_window_minutes": 60,
    "take_profit_price": None,           # Z; None = hold to resolution
    "stop_loss_delta": None,
    "min_leader_margin": 0.005,
    "max_entry_spread": 0.05,
    "max_exit_spread": 0.10,
    "hours_max": 6.0,
    "baseline_usdc": 5.0,
    "book_max_age_s": 120,
}


class LateLeader(Apricot):
    family = "late_leader"

    def p(self, sport=None):
        base = {k: v for k, v in self.base_params.items() if k != "sport_overrides"}
        if sport:
            base.update((self.base_params.get("sport_overrides") or {}).get(sport) or {})
        return {**DEFAULTS, **base}

    @staticmethod
    def closing_prices(view: MarketView, start: int, token_ids: list[str], window_min: float) -> list[float | None]:
        out = []
        for t in token_ids:
            bars = view.price_bars(t, start - int(window_min * 60), start - 60)
            out.append(bars[-1][1] if bars else None)
        return out

    @staticmethod
    def pregame_ok(pre: list[float | None], idx: int, prm: dict) -> tuple[bool, str]:
        x = prm.get("pregame_max")
        if x is None:
            return True, "ok"
        if str(prm.get("pregame_scope", "game")) == "token":
            v = pre[idx]
            if v is None:
                return False, "pregame_price_missing"
            return v <= float(x) + EPS, "pregame_above_max"
        if any(v is None for v in pre):
            return False, "pregame_price_missing"
        return max(pre) <= float(x) + EPS, "pregame_above_max"

    @staticmethod
    def crossed(view: MarketView, token_id: str, since: int, now: int, y: float) -> bool:
        """A canonical bar of the token below Y in [since, now - 60] (the leader came from below)."""
        return any(p < y - EPS for _, p in view.price_bars(token_id, since, now - 60))

    def _leader(self, books: dict[str, Book], labels: dict[str, str], prm: dict) -> tuple[str, str, dict]:
        for b in books.values():
            if b.spread is None or b.spread > float(prm["max_entry_spread"]) + EPS:
                return "spread_too_wide", "", {}
        lead = pick_leader(books, labels)
        if lead is None:
            return "no_leader", "", {}
        leader, margin = lead
        if margin + EPS < float(prm["min_leader_margin"]):
            return "leader_margin_too_small", leader, {}
        y = float(prm["trigger_price"])
        mid = books[leader].mid
        if mid is None or mid + EPS < y:
            return "below_trigger", leader, {}
        w = books[leader].walk_buy(float(prm["baseline_usdc"]))
        if not w.ok:
            return "incomplete_book", leader, {}
        lo, hi = y - float(prm["entry_floor_slack"]), y + float(prm["max_entry_premium"])
        if not (lo - EPS <= w.vwap <= hi + EPS):
            return "leader_outside_band", leader, {"vwap": w.vwap}
        return "ok", leader, {"vwap": w.vwap, "mid": mid, "margin": margin}

    def entry_signals(self, view: MarketView, now: int, ledger: Ledger) -> list[EntryIntent]:
        self.skips = []
        intents = []
        for sport in self.sports:
            if sport == "soccer":           # three-way result markets: out of scope (no draw-free market)
                continue
            prm = self.p(sport)
            for game in view.live_games(now, [sport], max_age_hours=float(prm["hours_max"])):
                if game.start_time is None or not game_in_scope(view, game, prm) or event_traded(ledger, game.game_key):
                    continue
                wall = (now - game.start_time) / 60.0
                t_min = float(prm["min_wall_minute"] or 0)
                if wall + EPS < t_min or (prm["max_wall_minute"] is not None and wall > float(prm["max_wall_minute"]) + EPS):
                    continue
                tokens = result_tokens(view, game)
                if tokens is None or len(tokens) != 2:
                    self.skip(game.game_key, "result_set_incomplete")
                    continue
                if not view.is_tradable(tokens[0].market, now):
                    continue
                ids = [rt.token_id for rt in tokens]
                books = self._event_books(view, now, ids, int(prm["book_max_age_s"]))
                if books is None:
                    continue
                labels = {rt.token_id: rt.kind for rt in tokens}
                status, leader, feat = self._leader(books, labels, prm)
                if status != "ok":
                    if status not in ("below_trigger",):
                        self.skip(game.game_key, status)
                    continue
                y = float(prm["trigger_price"])
                if prm["require_cross"] and not self.crossed(view, leader, game.start_time + int(t_min * 60), now, y):
                    self.skip(game.game_key, "no_cross_since_T")
                    continue
                pre = self.closing_prices(view, game.start_time, ids, float(prm["pregame_window_minutes"]))
                ok, why = self.pregame_ok(pre, ids.index(leader), prm)
                if not ok:
                    self.skip(game.game_key, why)
                    continue
                rt = next(r for r in tokens if r.token_id == leader)
                lo, hi = y - float(prm["entry_floor_slack"]), y + float(prm["max_entry_premium"])
                intents.append(EntryIntent(
                    token_id=leader, condition_id=rt.market.condition_id, game_key=game.game_key, sport=sport,
                    league=game.league, outcome_label=rt.token.outcome_label or rt.kind,
                    signal_price=feat["vwap"], min_price=lo, max_price=hi,
                    reason=f"wall {wall:.0f}m leader crossed {y:.2f} (vwap {feat['vwap']:.4f})",
                    exit_rules={"take_profit_price": prm["take_profit_price"],
                                "max_exit_spread": prm["max_exit_spread"],
                                "stop_loss_delta": prm["stop_loss_delta"]},
                    context_tokens=ids, game_minute=wall,
                    features={"wall_minute": round(wall, 2), **feat, "kind": rt.kind, "labels": labels,
                              "pregame": pre, "trigger_price": y},
                    priority=(wall, game.game_key)))
        intents.sort(key=lambda i: i.priority)
        return intents

    def confirm_entry(self, intent: EntryIntent, books: dict[str, Book], notional_usdc: float) -> Check:
        prm = self.p(intent.sport)
        ctx = {t: books.get(t) for t in intent.context_tokens}
        if any(b is None or b.best_bid is None or b.crossed for b in ctx.values()):
            return Check(False, "fresh_book_missing")
        status, leader, _ = self._leader(ctx, intent.features.get("labels") or {t: t for t in ctx}, prm)
        if status != "ok":
            return Check(False, f"fresh_{status}")
        if leader != intent.token_id:
            return Check(False, "leader_changed")
        return band_walk_check(ctx[leader], notional_usdc, intent.min_price, intent.max_price, self.min_order_shares)

    def _tp_check(self, position: PositionView, book: Book, fee_schedule) -> Check:
        tp = (position.exit_rules or {}).get("take_profit_price")
        if tp is None:
            return Check(False, "tp_disabled")       # hold to resolution (stop still applies)
        return net_positive_tp_check(position, book, float(tp), fee_schedule)
