"""goal_over: mechanically buy the Total 0.5 goals **Over** token ("at least one goal") for every in-scope soccer
game, pre-game, inside [kickoff - entry_minutes_before_max, kickoff - entry_minutes_before_min], when the executable
ask (full-book walk for the stake) is inside [price_min, price_max]. One entry per game.

Baseline arm of the AI 0-0 study (docs/research/llm-forecast-study.md): "buy everything" tells us what the
LLM/consensus selection adds over the market's own price.

Exits (frozen into exit_rules at entry; both off = hold to resolution, the default):
- take_profit_price: sell the whole holding once its bid VWAP >= the price AND the sale is net-positive after the
  buy and sell fees (base.net_positive_tp_check; unknown fee schedule never passes). Over 0.5 effectively resolves
  the moment a goal is scored (bids jump to ~0.99+ and stay), so a TP near 0.99 mostly recycles capital hours
  before the on-chain resolution/redeem rather than locking in extra edge.
- stop_loss_price: sell when the best bid <= the price (no goal yet late in the game -> Over decays toward 0).
Both thresholds are null by default; their `bounds` let autopilot switch them on (null -> value) within limits.

Book freshness: the collector polls Total 0.5 books every minute only from 15 min before kickoff (poll lead);
pre-game snapshots further out cover moneyline/draw only. With book_max_age_s=120 a 60->5 window is therefore
effectively 15->5 until the collector polls goal markets earlier.

In-play entry (allow_in_play, default off): only within in_play_max_minutes after the scheduled start and only
when a fresh game_state (<= state_max_age_s) shows 0-0 and the game is live, so a stale pre-goal book cannot
produce a phantom fill.
"""

from __future__ import annotations

from polylab.execution.fees import FeeSchedule, parse_fee_schedule
from polylab.marketview import EPS, Book, MarketView
from polylab.strategies.base import (Check, EntryIntent, ExitIntent, Ledger, PositionView, Strategy, event_traded,
                                     floor2, game_in_scope, net_positive_tp_check)

DEFAULTS = {
    "leagues": None,
    "min_game_volume_usd": None,
    "entry_minutes_before_max": 60,
    "entry_minutes_before_min": 5,
    "price_min": 0.50,
    "price_max": 0.985,
    "take_profit_price": None,          # None = off (hold to resolution)
    "stop_loss_price": None,            # None = off
    "take_profit_pct": None,            # e.g. 0.02: sell once the exit is +2% over the confirmed entry VWAP
    "stop_loss_pct": None,              # e.g. 0.10: sell once the best bid is 10% below the entry VWAP
    "hold_above_price": None,           # e.g. 0.99: at/above this bid neither TP nor SL fires (ride to resolution)
    "max_stop_spread": 0.10,
    "allow_in_play": False,
    "in_play_max_minutes": 30,
    "state_max_age_s": 300,
    "book_max_age_s": 120,
}


def over_0_5(view: MarketView, game_key: str) -> tuple[str, str] | None:
    """(condition_id, Over token) of the game's Total 0.5 goals market, if collected."""
    for m in view.markets(game_key, types=("total",)):
        if m.line is None or abs(m.line - 0.5) > 1e-9:
            continue
        for t in m.tokens:
            if t.side == "over" or (t.outcome_label or "").strip().lower() == "over":
                return m.condition_id, t.token_id
    return None


# ---------------------------------------------------------------- threshold exits (shared with llm_nil)

def threshold_exit_rules(prm: dict) -> dict:
    tp, sl = prm.get("take_profit_price"), prm.get("stop_loss_price")
    tpp, slp, hold = prm.get("take_profit_pct"), prm.get("stop_loss_pct"), prm.get("hold_above_price")
    return {"hold_to_resolution": tp is None and sl is None and tpp is None and slp is None,
            "take_profit_price": tp, "stop_loss_price": sl, "take_profit_pct": tpp, "stop_loss_pct": slp,
            "hold_above_price": hold, "max_stop_spread": prm.get("max_stop_spread", DEFAULTS["max_stop_spread"])}


def effective_thresholds(position: PositionView) -> tuple[float | None, float | None, float | None]:
    """(tp_price, sl_price, hold_above) for this position: relative rules resolve against the confirmed entry VWAP;
    an absolute price wins when both are set."""
    r = position.exit_rules or {}
    tp, sl = r.get("take_profit_price"), r.get("stop_loss_price")
    entry = position.entry_price
    if tp is None and r.get("take_profit_pct") is not None and entry:
        tp = round(entry * (1 + float(r["take_profit_pct"])), 6)
    if sl is None and r.get("stop_loss_pct") is not None and entry:
        sl = round(entry * (1 - float(r["stop_loss_pct"])), 6)
    hold = r.get("hold_above_price")
    return (float(tp) if tp is not None else None, float(sl) if sl is not None else None,
            float(hold) if hold is not None else None)


def threshold_exit(view: MarketView, now: int, position: PositionView, book_max_age_s: int) -> ExitIntent | None:
    """TP (net-positive full-holding bid VWAP >= take_profit_price) first, then stop (best bid <= stop_loss_price)."""
    tp, sl, hold = effective_thresholds(position)
    if (tp is None and sl is None) or position.status != "open" or not position.shares:
        return None
    book = view.book(position.token_id, now, max_age_s=book_max_age_s)
    if book is None or book.best_bid is None:
        return None
    if hold is not None and book.best_bid >= hold - EPS:
        return None  # near-certain (e.g. a goal already scored for Over 0.5): ride to resolution
    if tp is not None and (hold is None or tp < hold - EPS):
        m = view.market(position.condition_id)
        sched = parse_fee_schedule(m.fee_schedule) if m else None
        c = net_positive_tp_check(position, book, float(tp), sched)
        if c.ok:
            return ExitIntent(position.position_id, "take_profit", c.shares, float(tp),
                              f"full-holding bid vwap {c.walk_vwap:.4f} >= tp {tp} net-positive",
                              {"vwap": c.walk_vwap, "tp": tp, "fee_schedule": sched.__dict__ if sched else None})
    if sl is not None and book.best_bid <= float(sl) + EPS:
        shares = floor2(position.shares)
        if shares > 0:
            return ExitIntent(position.position_id, "stop_loss", shares, 0.001,
                              f"best_bid {book.best_bid} <= stop {sl}", {"best_bid": book.best_bid, "stop": sl})
    return None


def threshold_confirm_exit(position: PositionView, intent: ExitIntent, book: Book | None) -> Check:
    r = position.exit_rules or {}
    tp, sl, hold = effective_thresholds(position)
    if book is None or book.best_bid is None:
        return Check(False, "no_bids")
    if hold is not None and book.best_bid >= hold - EPS:
        return Check(False, "hold_above_price")
    if intent.kind == "take_profit":
        if tp is None:
            return Check(False, "tp_disabled")
        raw = intent.features.get("fee_schedule")
        return net_positive_tp_check(position, book, float(tp), FeeSchedule(**raw) if raw else None)
    if sl is None:
        return Check(False, "stop_disabled")
    if book.best_bid > float(sl) + EPS:
        return Check(False, "recovered_above_stop")
    spread = book.spread
    if spread is None or spread > float(r.get("max_stop_spread", DEFAULTS["max_stop_spread"])) + EPS:
        return Check(False, "stop_spread_too_wide")
    w = book.walk_sell(intent.shares)
    if not w.ok or not (0 < w.limit_price < 1):
        return Check(False, "insufficient_bid_depth")
    return Check(True, "ok", w.vwap, w.limit_price, w.shares)


# ---------------------------------------------------------------- strategy

class GoalOver(Strategy):
    family = "goal_over"

    def p(self, sport=None):
        return {**DEFAULTS, **super().p(sport)}

    def _in_play_ok(self, view: MarketView, game, now: int, prm: dict) -> bool:
        state = view.game_state(game.game_key, now)
        if state is None or now - state.ts > int(prm["state_max_age_s"]) or state.ended or state.live is False:
            return False
        return state.home_score == 0 and state.away_score == 0

    def entry_signals(self, view: MarketView, now: int, ledger: Ledger) -> list[EntryIntent]:
        self.skips = []
        prm = self.p("soccer")
        stake = float(getattr(self.variant, "stake_usdc", None) or 5.0)
        lo_s, hi_s = float(prm["entry_minutes_before_min"]) * 60, float(prm["entry_minutes_before_max"]) * 60
        games = [g for g in view.upcoming_games(now, hi_s / 3600, ["soccer"])
                 if g.start_time is not None and lo_s <= g.start_time - now <= hi_s]
        if prm["allow_in_play"]:
            games += [g for g in view.live_games(now, ["soccer"], float(prm["in_play_max_minutes"]) / 60)
                      if g.start_time is not None and now - g.start_time <= float(prm["in_play_max_minutes"]) * 60]
        intents = []
        for game in games:
            if not game_in_scope(view, game, prm):
                continue
            gk = game.game_key
            if event_traded(ledger, gk):
                continue
            in_play = game.start_time <= now
            if in_play and not self._in_play_ok(view, game, now, prm):
                self.skip(gk, "in_play_state_not_fresh_0_0")
                continue
            ov = over_0_5(view, gk)
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
            lo, hi = float(prm["price_min"]), float(prm["price_max"])
            if not (lo - EPS <= w.vwap <= hi + EPS) or w.limit_price > hi + EPS:
                self.skip(gk, "price_out_of_band")
                continue
            to_kick = game.start_time - now
            intents.append(EntryIntent(
                token_id=ov[1], condition_id=ov[0], game_key=gk, sport="soccer", league=game.league,
                outcome_label="Over 0.5", signal_price=w.vwap, min_price=lo, max_price=hi,
                reason=f"Over 0.5 ask vwap {w.vwap:.4f} in [{lo}, {hi}] ({to_kick / 60:+.0f} min to kickoff)",
                exit_rules=threshold_exit_rules(prm), context_tokens=[ov[1]],
                features={"ask_vwap": w.vwap, "limit_price": w.limit_price, "implied_p00": round(1 - w.vwap, 4),
                          "minutes_to_kickoff": round(to_kick / 60, 2), "in_play": in_play},
                priority=(game.start_time, gk)))
        intents.sort(key=lambda i: i.priority)
        return intents

    def exit_signals(self, view: MarketView, now: int, position: PositionView) -> ExitIntent | None:
        return threshold_exit(view, now, position, int(self.p(position.sport)["book_max_age_s"]))

    def confirm_exit(self, position: PositionView, intent: ExitIntent, book: Book | None) -> Check:
        return threshold_confirm_exit(position, intent, book)
