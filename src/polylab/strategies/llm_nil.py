"""llm_nil: buy the Total 0.5 goals Over token ("this game will not end 0-0") pre-game for the top-k daily AI
forecasts when the AI probability beats the ask by `edge`; hold to resolution unless threshold exits are on.

Research side study, docs/research/llm-forecast-study.md. Forecasts come from `polylab forecast`
(research/llm_forecasts.db) via the same canonical rules the evaluation uses, as of `now`:
- params.source = "claude" (default, llm-nil-draw control): the Claude engine's canonical forecasts
  (latest successful Claude run created before kickoff), rank = Claude's own top list.
- params.source = "consensus" (llm-nil-consensus): the Claude+ChatGPT consensus (pre-registered rule in
  llm_forecast.compute_consensus); rank = consensus rank, AI P(not 0-0) = 1 - consensus P(0-0). The entry test
  "market implied P(0-0) - consensus P(0-0) >= edge" with implied P(0-0) = 1 - ask is the same inequality.
The Over token is looked up by game_key at entry time, because the Total 0.5 market can be collected after the
forecast was made. Over books are only polled from 15 min before kickoff (collector poll lead), so entries happen in
[kickoff - entry_window_minutes, kickoff).

Exits: take_profit_price / stop_loss_price (goal_over.threshold_exit, frozen at entry), both null by default.

Live guard: only the variant ids in LIVE_ALLOWED_IDS with source=consensus may run mode=live (the thesis owner's
explicit decision for llm-nil-consensus). Everything else in this family, llm-nil-draw included, refuses live.
"""

from __future__ import annotations

from typing import Callable

from polylab.marketview import EPS, MarketView
from polylab.strategies.base import Check, EntryIntent, Ledger, PositionView, Strategy, event_traded
from polylab.strategies.goal_over import threshold_confirm_exit, threshold_exit, threshold_exit_rules

LIVE_ALLOWED_IDS = frozenset({"llm-nil-consensus"})
SOURCES = ("claude", "consensus")

DEFAULTS = {
    "top_k": 3,
    "edge": 0.02,
    "max_price": 0.96,
    "min_price": 0.50,
    "entry_window_minutes": 15,
    "baseline_usdc": 5.0,
    "book_max_age_s": 120,
    "leagues": None,
    "source": "claude",
    "take_profit_price": None,
    "stop_loss_price": None,
    "max_stop_spread": 0.10,
}


def _default_source(now: int, source: str = "claude") -> dict[str, dict]:
    """Canonical forecasts (or consensus) from the runtime research DB; {} when storage or the DB is unavailable."""
    try:
        from polylab import settings  # noqa: PLC0415
        from polylab.research import llm_forecast as lf  # noqa: PLC0415
        path = lf.db_path(settings.paths())
        if source == "consensus":
            return lf.load_consensus_forecasts(path, now)
        return lf.load_canonical(path, now, engine="claude")
    except Exception:
        return {}


class LlmNil(Strategy):
    family = "llm_nil"
    forecast_source: Callable[[int], dict[str, dict]] = staticmethod(_default_source)

    def __init__(self, params, variant=None):
        source = (params or {}).get("source", DEFAULTS["source"])
        if source not in SOURCES:
            raise ValueError(f"llm_nil: unknown source {source!r}")
        if getattr(variant, "mode", None) == "live" and not (
                getattr(variant, "id", None) in LIVE_ALLOWED_IDS and source == "consensus"):
            raise ValueError("llm_nil is paper-only except llm-nil-consensus (source=consensus); refusing mode=live")
        super().__init__(params, variant)

    def p(self, sport=None):
        return {**DEFAULTS, **super().p(sport)}

    def _forecasts(self, now: int, source: str) -> dict[str, dict]:
        if self.forecast_source is _default_source:
            return _default_source(now, source)
        return self.forecast_source(now)        # injected (tests, backtests)

    def entry_signals(self, view: MarketView, now: int, ledger: Ledger) -> list[EntryIntent]:
        from polylab.research.llm_forecast import over_token  # noqa: PLC0415
        self.skips = []
        prm = self.p("soccer")
        forecasts = self._forecasts(now, prm["source"])
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
                reason=f"{prm['source']} P(not 0-0) {ai:.3f} - ask {w.vwap:.3f} >= edge {prm['edge']} (rank {rank})",
                exit_rules={**threshold_exit_rules(prm), "ai_prob": ai, "run_id": f.get("run_id")},
                context_tokens=[ov[1]],
                features={"ai_prob": ai, "rank": rank, "run_id": f.get("run_id"), "ask_vwap": w.vwap,
                          "edge": round(ai - w.vwap, 4), "minutes_to_kickoff": round(to_kick / 60, 2),
                          "market_price_at_forecast": f.get("market_price_at_forecast"), "source": prm["source"],
                          "p00_claude": f.get("p00_claude"), "p00_codex": f.get("p00_codex"),
                          "implied_p00": round(1 - w.vwap, 4)},
                priority=(rank, game.start_time, gk)))
        intents.sort(key=lambda i: i.priority)
        return intents

    def exit_signals(self, view: MarketView, now: int, position: PositionView):
        # hold to resolution unless TP/SL were frozen into exit_rules; the engine settles from core.db
        return threshold_exit(view, now, position, int(self.p(position.sport)["book_max_age_s"]))

    def confirm_exit(self, position: PositionView, intent, book) -> Check:
        return threshold_confirm_exit(position, intent, book)
