"""ai_ou05: rest fee-free maker bids on the soccer Total 0.5 goals market for the games both AIs picked.

Owner decision 2026-10-10 `ai-ou05:red-live` (docs/research/ai-ou05-study.md). The daily forecast run
(`polylab forecast`, research/llm_forecasts.db) freezes `ou05_picks`: inside Europe's top-5 leagues + MLS, games both
Claude and ChatGPT rank among their lowest P(0-0) are Over picks ("at least one goal"), those both rank among their
highest P(0-0) are Under picks ("0-0"). This strategy only reads those rows (canonical_ou05_picks) and:

- buys the picked side's token with a post-only resting BUY (order_style must be maker: no taker entries, so no
  fee), from the pick until `maker_entry_cutoff_minutes` before kickoff, re-quoted by the maker engine;
- only when the market is not over-priced as a whole: overround = Over ask + Under ask - 1 <= max_overround
  (the owner skips sums >= 1.05). Not a flat gate (owner 2026-10-10 `ai-ou05:overround-priority`): the further the
  sum is from 1.0 the more both sides lose, so tight books come first — intents are ordered by overround. Every
  entry rests one tick BELOW the best bid (`entry_price_rule: below`, owner 2026-10-10 `ai-ou05:bid-below`): the
  price is already over-valued, so the owner never pays up — cheaper, later or never filled. `overround_tier`
  (<= tight_overround) is recorded for the analysis only;
- only inside the side's price band, and never on a condition the account's owner already holds outside this
  ledger (red is also the owner's manual wallet: a redeem pays a condition's whole holding in one transaction);
- holds to resolution: no take-profit, no stop-loss (the study measures calibration at settlement).

A resting entry is withdrawn when a later pick batch no longer picks the game for that side, when the overround gate
fails on a fresh book, or at the cutoff; filled positions are kept.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

from polylab.marketview import EPS, Book, MarketView
from polylab.strategies.base import Check, EntryIntent, ExitIntent, Ledger, PositionView, Strategy, event_traded

SIDES = ("over", "under")
OUTCOME = {"over": "Over 0.5", "under": "Under 0.5"}

DEFAULTS = {
    "sides": ["over", "under"],
    "leagues": None,
    "max_overround": 0.04,
    "tight_overround": 0.01,
    "entry_price_rule": "below",
    "over_price_min": 0.80,
    "over_price_max": 0.99,
    "under_price_min": 0.01,
    "under_price_max": 0.20,
    "book_max_age_s": 900,
    "owner_holdings_max_age_s": 7200,
}


def _default_picks(now: int) -> dict[str, dict]:
    """Canonical O/U 0.5 picks from the runtime research DB; {} when storage or the DB is unavailable."""
    try:
        from polylab import settings  # noqa: PLC0415
        from polylab.research import llm_forecast as lf  # noqa: PLC0415
        return lf.load_ou05_picks(lf.db_path(settings.paths()), now)
    except Exception:
        return {}


def _default_owner_conditions(alias: str, now: int, max_age_s: int) -> set[str] | None:
    """Conditions the wallet currently holds per its watch ledger (data/manual/<alias>.db, refreshed every 15 min by
    polylab manual sync). None = unknown (no ledger or snapshot older than max_age_s): callers fail closed."""
    try:
        from polylab import settings  # noqa: PLC0415
        from polylab.manual import ledger as manual  # noqa: PLC0415
        path = manual.db_path(settings.paths(), alias)
        if not Path(path).exists():
            return None
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            rows = conn.execute("SELECT condition_id, current_size, fetched_at FROM api_positions").fetchall()
            synced = conn.execute("SELECT value FROM meta WHERE key='last_sync_at'").fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    last = int(synced[0]) if synced and synced[0] else max((r[2] for r in rows), default=0)
    if not last or now - last > max_age_s:
        return None
    return {r[0] for r in rows if r[0] and (r[1] or 0) > 0}


class AiOu05(Strategy):
    family = "ai_ou05"
    picks_source: Callable[[int], dict[str, dict]] = staticmethod(_default_picks)
    owner_conditions: Callable[[str, int, int], set[str] | None] = staticmethod(_default_owner_conditions)

    def __init__(self, params, variant=None):
        super().__init__(params, variant)
        self._picks_at: tuple[int, dict[str, dict]] | None = None

    def p(self, sport=None):
        return {**DEFAULTS, **super().p(sport)}

    def _picks(self, now: int) -> dict[str, dict]:
        if self._picks_at is None or self._picks_at[0] != now:
            self._picks_at = (now, self.picks_source(now))
        return self._picks_at[1]

    def _band(self, prm: dict, side: str) -> tuple[float, float]:
        return float(prm[f"{side}_price_min"]), float(prm[f"{side}_price_max"])

    @staticmethod
    def overround(over: Book | None, under: Book | None) -> float | None:
        if over is None or under is None or over.best_ask is None or under.best_ask is None:
            return None
        return round(over.best_ask + under.best_ask - 1, 6)

    def entry_signals(self, view: MarketView, now: int, ledger: Ledger) -> list[EntryIntent]:
        self.skips = []
        prm = self.p("soccer")
        if self.order_style("soccer") != "maker":
            self.skip("*", "maker_only_strategy")          # the owner's rule: never pay the taker fee
            return []
        picks = self._picks(now)
        if not picks:
            return []
        sides = {s for s in (prm.get("sides") or []) if s in SIDES}
        leagues = {x.lower() for x in (prm.get("leagues") or [])}
        cutoff = float(self.execution("soccer")["maker_entry_cutoff_minutes"]) * 60
        own = {p.condition_id for p in ledger.positions}
        alias = getattr(self.variant, "account", None)
        held: set[str] | None = set()
        if alias:
            held = self.owner_conditions(alias, now, int(prm["owner_holdings_max_age_s"]))
        intents = []
        for gk, pk in sorted(picks.items()):
            side = pk.get("side")
            if side not in sides:
                continue
            game = view.game(gk)
            if game is None or game.start_time is None or game.start_time - now <= cutoff:
                continue
            if leagues and (game.league or "").lower() not in leagues:
                continue
            if event_traded(ledger, gk):
                continue
            cid, token = pk["condition_id"], pk[f"{side}_token"]
            if held is None:
                self.skip(gk, "owner_holdings_unknown")      # fail closed: cannot rule out a shared condition
                continue
            if cid in held and cid not in own:
                self.skip(gk, "owner_holds_condition")
                continue
            market = view.market(cid)
            if market is None or not view.is_tradable(market, now):
                self.skip(gk, "ou05_market_not_tradable")
                continue
            age = int(prm["book_max_age_s"])
            over_b, under_b = view.book(pk["over_token"], now, max_age_s=age), view.book(pk["under_token"], now,
                                                                                         max_age_s=age)
            book = over_b if side == "over" else under_b
            if book is None or book.crossed or book.best_bid is None or book.best_ask is None:
                self.skip(gk, "no_fresh_book")
                continue
            ovr = self.overround(over_b, under_b)
            if ovr is None:
                self.skip(gk, "overround_unknown")
                continue
            if ovr > float(prm["max_overround"]) + EPS:
                self.skip(gk, "overround_too_high")
                continue
            lo, hi = self._band(prm, side)
            if book.best_ask < lo - EPS or book.best_bid > hi + EPS:    # the maker price re-checks the band
                self.skip(gk, "price_out_of_band")
                continue
            to_kick = game.start_time - now
            tight = ovr <= float(prm["tight_overround"]) + EPS
            rule = str(prm["entry_price_rule"])
            intents.append(EntryIntent(
                token_id=token, condition_id=cid, game_key=gk, sport="soccer", league=game.league,
                outcome_label=OUTCOME[side], signal_price=book.best_ask, min_price=lo, max_price=hi,
                reason=(f"{side} pick {pk.get('pick_rank')} (P(0-0) Claude {pk['p00_claude']:.3f} · ChatGPT "
                        f"{pk['p00_codex']:.3f}); sum ask {1 + ovr:.3f} <= {1 + float(prm['max_overround']):.2f}"),
                exit_rules={"hold_to_resolution": True, "take_profit_price": None, "stop_loss_price": None,
                            "side": side, "pick_batch": pk.get("batch_id")},
                context_tokens=[pk["over_token"], pk["under_token"]],
                features={"side": side, "pick_rank": pk.get("pick_rank"), "pick_batch": pk.get("batch_id"),
                          "p00_claude": pk["p00_claude"], "p00_codex": pk["p00_codex"], "p00_mean": pk["p00_mean"],
                          "overround": ovr, "best_bid": book.best_bid, "best_ask": book.best_ask,
                          "implied_p00": round(book.best_ask if side == "under" else 1 - book.best_ask, 4),
                          "overround_at_pick": pk.get("overround"), "minutes_to_kickoff": round(to_kick / 60, 2),
                          "overround_tier": "tight" if tight else "wide", "maker_price_rule": rule},
                priority=(ovr, pk.get("pick_rank") or 99, game.start_time, gk)))
        intents.sort(key=lambda i: i.priority)
        return intents

    # --- maker hooks
    def maker_entry_open(self, view: MarketView, now: int, position: PositionView) -> bool:
        if not super().maker_entry_open(view, now, position):
            return False
        pk = self._picks(now).get(position.game_key or "")
        side = (position.exit_rules or {}).get("side")
        if pk is None or pk.get("side") != side or pk.get(f"{side}_token") != position.token_id:
            return False                                      # a later batch dropped the pick
        age = int(self.p(position.sport)["book_max_age_s"])
        ovr = self.overround(view.book(pk["over_token"], now, max_age_s=age),
                             view.book(pk["under_token"], now, max_age_s=age))
        return ovr is None or ovr <= float(self.p(position.sport)["max_overround"]) + EPS

    # --- exits: none (hold to resolution; the engine settles from core.db)
    def exit_signals(self, view: MarketView, now: int, position: PositionView) -> ExitIntent | None:
        return None

    def confirm_exit(self, position: PositionView, intent: ExitIntent, book: Book | None) -> Check:
        return Check(False, "hold_to_resolution")
