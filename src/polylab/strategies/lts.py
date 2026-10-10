"""LTS — late threshold stability (owner request 2026-10-10 `hypothesis:lts`; pre-registration and results in
docs/research/hypothesis-lts.md, evaluator polylab.analysis.lts — same rules on the same canonical bars).

Entry, one ATTEMPT per game (a cancelled, unfilled bid is not retried): at wall-clock minute >= `min_wall_minute` (T)
after the scheduled start, the first cycle where the leader — the higher of the two team tokens (US moneyline; soccer:
the home / away team-win Yes tokens, a draw loses) by at least `min_leader_margin` — has a fresh book mid inside
[`trigger_price` (Y), min(Y + `band_width`, `band_cap`)]. Maker only: the variant must run `order_style: maker` (no
taker entry is ever sent, so no fee); the bid rests ONE TICK BELOW the best bid (`maker_price_rule: below`, frozen
per intent) and is never re-quoted (execution `maker_reprice_ticks` / `maker_ttl_minutes` are set out of reach in
the yaml; the intent band is wide so a rising price does not cancel it through the re-quote path).

Cancel (maker_entry_open, checked after the cycle's fills are booked): after `max_wait_minutes` (None = until the game
ends), when the game is over, at scheduled start + `hours_max`, or when the leader's mid is below Y - `floor_c`
(non-binding by construction: a bar below a floor under the limit has already filled the bid).
Exit: none — held to resolution (no take-profit, no stop).
Clock: wall minutes since the scheduled start, identical in backtest and live (no historical game clock).
"""

from __future__ import annotations

from polylab.marketview import EPS, Book, MarketView
from polylab.strategies.base import (Check, EntryIntent, ExitIntent, Ledger, PositionView, Strategy, game_in_scope,
                                     result_tokens)

DEFAULTS = {
    "min_wall_minute": 0,                # T
    "trigger_price": 0.80,               # Y
    "band_width": 0.03,                  # delta: mid in [Y, Y + delta]
    "band_cap": 0.995,
    "max_wait_minutes": None,            # W; None = rest until the game ends
    "floor_c": 0.05,
    "min_leader_margin": 0.005,
    "hours_max": 5.0,
    "book_max_age_s": 120,
}
INTENT_BAND = (0.01, 0.999)              # resting-price band for the engine's re-quote path (never re-quoted)


def attempted(ledger: Ledger, game_key: str | None) -> bool:
    """Any earlier position on the game, whatever its status (unfilled / failed included): one attempt per game."""
    return any(p.game_key == game_key for p in ledger.positions)


class Lts(Strategy):
    family = "lts"
    backtest_fill_model = "maker_bars"   # polylab backtest / retro replays: bar trade-through maker fills

    def p(self, sport=None):
        return {**DEFAULTS, **super().p(sport)}

    @staticmethod
    def band(prm: dict) -> tuple[float, float]:
        y = float(prm["trigger_price"])
        return y, round(min(y + float(prm["band_width"]), float(prm["band_cap"])), 6)

    @staticmethod
    def team_tokens(view: MarketView, game) -> list | None:
        tokens = result_tokens(view, game)
        if tokens is None:
            return None
        team = [t for t in tokens if t.is_yes and t.kind in ("HOME", "AWAY")]
        return team if len(team) == 2 else None

    def _leader(self, view: MarketView, now: int, team, prm: dict) -> tuple[str, object, Book | None, dict]:
        age = int(prm["book_max_age_s"])
        books = {rt.token_id: view.book(rt.token_id, now, max_age_s=age) for rt in team}
        if any(b is None or b.crossed or b.mid is None for b in books.values()):
            return "no_fresh_book", None, None, {}
        a, b = sorted(team, key=lambda rt: -books[rt.token_id].mid)
        margin = books[a.token_id].mid - books[b.token_id].mid
        if margin + EPS < float(prm["min_leader_margin"]):
            return "leader_margin_too_small", None, None, {}
        return "ok", a, books[a.token_id], {"margin": round(margin, 6)}

    def entry_signals(self, view: MarketView, now: int, ledger: Ledger) -> list[EntryIntent]:
        self.skips = []
        intents = []
        for sport in self.sports:
            if self.order_style(sport) != "maker":
                self.skip(sport, "maker_only_strategy")      # owner rule: never pay the taker fee
                continue
            prm = self.p(sport)
            lo, hi = self.band(prm)
            for game in view.live_games(now, [sport], max_age_hours=float(prm["hours_max"])):
                if game.start_time is None or attempted(ledger, game.game_key) or not game_in_scope(view, game, prm):
                    continue
                wall = (now - game.start_time) / 60.0
                if wall + EPS < float(prm["min_wall_minute"] or 0):
                    continue
                team = self.team_tokens(view, game)
                if team is None:
                    self.skip(game.game_key, "result_set_incomplete")
                    continue
                if not view.is_tradable(team[0].market, now) or not view.is_tradable(team[1].market, now):
                    continue
                status, lead, book, feat = self._leader(view, now, team, prm)
                if status != "ok":
                    continue
                if not (lo - EPS <= book.mid <= hi + EPS):
                    continue
                intents.append(EntryIntent(
                    token_id=lead.token_id, condition_id=lead.market.condition_id, game_key=game.game_key,
                    sport=sport, league=game.league, outcome_label=lead.token.outcome_label or lead.kind,
                    signal_price=book.mid, min_price=INTENT_BAND[0], max_price=INTENT_BAND[1],
                    reason=f"wall {wall:.0f}m leader mid {book.mid:.3f} in [{lo:.2f}, {hi:.3f}]",
                    exit_rules={"hold_to_resolution": True, "take_profit_price": None, "stop_loss_price": None,
                                "trigger_price": lo, "floor_c": prm["floor_c"],
                                "max_wait_minutes": prm["max_wait_minutes"], "hours_max": prm["hours_max"]},
                    context_tokens=[rt.token_id for rt in team], game_minute=wall,
                    features={"wall_minute": round(wall, 2), "mid": book.mid, "best_bid": book.best_bid,
                              "best_ask": book.best_ask, "kind": lead.kind, "trigger_price": lo, "band_hi": hi,
                              "maker_price_rule": "below", **feat},
                    priority=(wall, game.game_key)))
        intents.sort(key=lambda i: i.priority)
        return intents

    # --- maker hooks
    def maker_entry_open(self, view: MarketView, now: int, position: PositionView) -> bool:
        if not super().maker_entry_open(view, now, position):
            return False
        rules = position.exit_rules or {}
        wait = rules.get("max_wait_minutes")
        if wait is not None and now - position.opened_at >= float(wait) * 60:
            return False
        game = view.game(position.game_key) if position.game_key else None
        if game is None or game.start_time is None or view.game_over(game, now):
            return False
        hours = float(rules.get("hours_max") or self.p(position.sport)["hours_max"])
        if now >= game.start_time + hours * 3600:
            return False
        y = rules.get("trigger_price")
        book = view.book(position.token_id, now, max_age_s=int(self.p(position.sport)["book_max_age_s"]))
        if y is not None and book is not None and book.mid is not None \
                and book.mid < float(y) - float(rules.get("floor_c") or 0) - EPS:
            return False
        return True

    # --- exits: none (held to resolution; the engine settles from core.db)
    def exit_signals(self, view: MarketView, now: int, position: PositionView) -> ExitIntent | None:
        return None

    def confirm_entry(self, intent: EntryIntent, books: dict[str, Book], notional_usdc: float) -> Check:
        return Check(False, "maker_only_strategy")      # the taker path is never used

    def confirm_exit(self, position: PositionView, intent: ExitIntent, book: Book | None) -> Check:
        return Check(False, "hold_to_resolution")
