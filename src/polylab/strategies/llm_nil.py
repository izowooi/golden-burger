"""llm_nil (PAPER ONLY): buy the Total 0.5 goals Over token ("this game will not end 0-0") pre-game for the LLM's
top-k daily forecasts when the AI probability beats the ask by `edge`; hold to resolution (no exits).

Research side study, docs/research/llm-forecast-study.md. Forecasts come from `polylab forecast`
(research/llm_forecasts.db) via the same canonical rule the evaluation uses (latest successful run created before
kickoff, as of `now`). The Over token is looked up by game_key at entry time, because the Total 0.5 market can be
collected after the forecast was made. Over books are only polled from 15 min before kickoff (collector poll lead),
so entries happen in [kickoff - entry_window_minutes, kickoff).

This family refuses live mode outright: it is a paper research arm and must never trade real money.
"""

from __future__ import annotations

from typing import Callable

from polylab.marketview import EPS, MarketView
from polylab.strategies.base import EntryIntent, Ledger, PositionView, Strategy, event_traded

DEFAULTS = {
    "top_k": 3,
    "edge": 0.02,
    "max_price": 0.96,
    "min_price": 0.50,
    "entry_window_minutes": 15,
    "baseline_usdc": 5.0,
    "book_max_age_s": 120,
    "leagues": None,
}


def _default_source(now: int) -> dict[str, dict]:
    """Canonical forecasts from the runtime research DB; {} when storage or the DB is unavailable."""
    try:
        from polylab import settings  # noqa: PLC0415
        from polylab.research.llm_forecast import db_path, load_canonical  # noqa: PLC0415
        return load_canonical(db_path(settings.paths()), now)
    except Exception:
        return {}


class LlmNil(Strategy):
    family = "llm_nil"
    forecast_source: Callable[[int], dict[str, dict]] = staticmethod(_default_source)

    def __init__(self, params, variant=None):
        if getattr(variant, "mode", None) == "live":
            raise ValueError("llm_nil is a paper-only research family; refusing mode=live")
        super().__init__(params, variant)

    def p(self, sport=None):
        return {**DEFAULTS, **super().p(sport)}

    def entry_signals(self, view: MarketView, now: int, ledger: Ledger) -> list[EntryIntent]:
        from polylab.research.llm_forecast import over_token  # noqa: PLC0415
        self.skips = []
        prm = self.p("soccer")
        forecasts = self.forecast_source(now)
        if not forecasts:
            return []
        leagues = {x.lower() for x in (prm.get("leagues") or [])}
        stake = float(getattr(self.variant, "stake_usdc", None) or prm["baseline_usdc"])
        intents = []
        for gk, f in sorted(forecasts.items()):
            rank = f.get("rank")
            if not rank or rank > int(prm["top_k"]):
                continue
            game = view.game(gk)
            if game is None or game.start_time is None:
                continue
            to_kick = game.start_time - now
            if not 0 < to_kick <= float(prm["entry_window_minutes"]) * 60:
                continue
            if leagues and (game.league or "").lower() not in leagues:
                continue
            if event_traded(ledger, gk):
                continue
            ov = over_token(view, gk)
            if ov is None:
                self.skip(gk, "no_over_0_5_market")
                continue
            market = view.market(ov[0])
            if market is None or not view.is_tradable(market, now):
                self.skip(gk, "over_market_not_tradable")
                continue
            book = view.book(ov[1], now, max_age_s=int(prm["book_max_age_s"]))
            if book is None or book.crossed:
                self.skip(gk, "no_fresh_over_book")
                continue
            w = book.walk_buy(stake)
            if not w.ok:
                self.skip(gk, "insufficient_ask_depth")
                continue
            ai = float(f["ai_prob"])
            hi = round(min(float(prm["max_price"]), ai - float(prm["edge"])), 4)
            if w.vwap > hi + EPS or w.vwap < float(prm["min_price"]) - EPS:
                self.skip(gk, "no_edge_or_price_out_of_band")
                continue
            intents.append(EntryIntent(
                token_id=ov[1], condition_id=ov[0], game_key=gk, sport="soccer", league=game.league,
                outcome_label="Over 0.5", signal_price=w.vwap, min_price=float(prm["min_price"]), max_price=hi,
                reason=f"AI P(not 0-0) {ai:.3f} - ask {w.vwap:.3f} >= edge {prm['edge']} (rank {rank})",
                exit_rules={"hold_to_resolution": True, "ai_prob": ai, "run_id": f.get("run_id")},
                context_tokens=[ov[1]],
                features={"ai_prob": ai, "rank": rank, "run_id": f.get("run_id"), "ask_vwap": w.vwap,
                          "edge": round(ai - w.vwap, 4), "minutes_to_kickoff": round(to_kick / 60, 2),
                          "market_price_at_forecast": f.get("market_price_at_forecast")},
                priority=(rank, game.start_time, gk)))
        intents.sort(key=lambda i: i.priority)
        return intents

    def exit_signals(self, view: MarketView, now: int, position: PositionView):
        return None     # hold to resolution; the engine settles from core.db
