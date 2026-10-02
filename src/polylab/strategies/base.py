"""Pure strategy interface.

A strategy reads a MarketView (read-only, as-of `now`) plus its own ledger history and
returns intents. It never talks to the network or writes anything. The engine executes
intents after a fresh-book re-check (`confirm_entry` / `confirm_exit`) and risk caps.

Stake sizing is not the strategy's job: the engine buys `variant.stake_usdc` (one ladder
tier) and passes that notional to `confirm_entry` so the band/limit checks use the real
order size.
"""

from __future__ import annotations

import copy
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from polylab.execution.fees import fee_usdc
from polylab.marketview import Book, Game, Market, MarketView, Token, EPS

OPEN_STATUSES = ("pending", "open", "closing", "quarantined")


@dataclass
class PositionView:
    """A strategy-DB position row as seen by strategies (open and recent closed)."""
    position_id: str
    status: str                  # pending | open | closing | closed | resolved | quarantined
    token_id: str
    condition_id: str
    game_key: str | None
    sport: str | None
    opened_at: int
    entry_price: float | None    # confirmed VWAP
    shares: float | None         # confirmed shares still held
    cost_usdc: float | None      # confirmed spend incl. fee
    exit_rules: dict[str, Any] = field(default_factory=dict)
    closed_at: int | None = None
    exit_reason: str | None = None
    game_minute_at_entry: float | None = None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES


@dataclass
class Ledger:
    """What a strategy may know about its own past: positions and permanent skips."""
    positions: list[PositionView] = field(default_factory=list)
    blocked_conditions: set[str] = field(default_factory=set)   # e.g. cherry rapid_jump

    @property
    def open_positions(self) -> list[PositionView]:
        return [p for p in self.positions if p.is_open]


@dataclass
class EntryIntent:
    token_id: str
    condition_id: str
    game_key: str | None
    sport: str
    league: str | None
    outcome_label: str | None
    signal_price: float                 # the price the band was checked on
    min_price: float                    # band low (re-checked on the fresh walk)
    max_price: float                    # band high == FOK limit ceiling
    reason: str
    exit_rules: dict[str, Any]          # frozen at entry, stored on the position
    context_tokens: list[str] = field(default_factory=list)   # books needed for re-check
    game_minute: float | None = None
    features: dict[str, Any] = field(default_factory=dict)
    priority: tuple = ()


@dataclass
class ExitIntent:
    position_id: str
    kind: str                           # take_profit | stop_loss | trailing_stop | time_exit
    shares: float                       # shares to sell (full holding unless partial TP)
    min_price: float                    # FOK SELL limit floor (worst bid accepted)
    reason: str
    features: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Check:
    ok: bool
    reason: str
    walk_vwap: float | None = None
    limit_price: float | None = None
    shares: float | None = None


class Strategy:
    family = "base"
    min_order_shares = 5.0

    def __init__(self, params: dict[str, Any], variant: Any = None):
        self.base_params = copy.deepcopy(params or {})
        self.variant = variant
        self.sports = list(getattr(variant, "sports", None) or self.base_params.get("sports") or [])
        self.skips: list[tuple[str, str]] = []

    # --- params
    def p(self, sport: str | None = None) -> dict[str, Any]:
        """Params with `sport_overrides.<sport>` merged in."""
        out = {k: v for k, v in self.base_params.items() if k != "sport_overrides"}
        if sport:
            out.update((self.base_params.get("sport_overrides") or {}).get(sport) or {})
        return out

    def skip(self, key: str, reason: str) -> None:
        self.skips.append((key, reason))

    # --- interface
    def entry_signals(self, view: MarketView, now: int, ledger: Ledger) -> list[EntryIntent]:
        raise NotImplementedError

    def exit_signals(self, view: MarketView, now: int, position: PositionView) -> ExitIntent | None:
        raise NotImplementedError

    def confirm_entry(self, intent: EntryIntent, books: dict[str, Book], notional_usdc: float) -> Check:
        """Default fresh-book re-check: the real-size walk stays inside the band."""
        book = books.get(intent.token_id)
        return band_walk_check(book, notional_usdc, intent.min_price, intent.max_price, self.min_order_shares)

    def confirm_exit(self, position: PositionView, intent: ExitIntent, book: Book | None) -> Check:
        """Default: the full intended size must be walkable at or above the limit floor."""
        if book is None or not book.bids:
            return Check(False, "no_bids")
        w = book.walk_sell(intent.shares)
        if not w.ok:
            return Check(False, "insufficient_bid_depth")
        if w.limit_price + EPS < intent.min_price:
            return Check(False, "limit_below_floor", w.vwap, w.limit_price, w.shares)
        return Check(True, "ok", w.vwap, w.limit_price, w.shares)


# ---------------------------------------------------------------- shared helpers

def band_walk_check(book: Book | None, notional: float, lo: float, hi: float,
                    min_shares: float = 5.0) -> Check:
    if book is None:
        return Check(False, "no_book")
    if book.crossed:
        return Check(False, "crossed_book")
    w = book.walk_buy(notional)
    if not w.ok:
        return Check(False, "insufficient_ask_depth")
    if w.limit_price > hi + EPS:
        return Check(False, "limit_above_band", w.vwap, w.limit_price, w.shares)
    if not (lo - EPS <= w.vwap <= hi + EPS):
        return Check(False, "vwap_outside_band", w.vwap, w.limit_price, w.shares)
    if w.shares + EPS < min_shares:
        return Check(False, "below_min_order_size", w.vwap, w.limit_price, w.shares)
    return Check(True, "ok", w.vwap, w.limit_price, w.shares)


def net_positive_tp_check(position: PositionView, book: Book, threshold: float, fee_schedule) -> Check:
    """Full-holding take-profit gate shared by strategies.

    OK only when the whole confirmed holding walks the bids at a VWAP >= threshold AND the
    proceeds after the sell fee exceed the confirmed cost (which already includes the buy fee).
    An unknown fee schedule never passes (fees are not assumed zero).
    """
    shares = floor2(position.shares or 0)
    if shares <= 0 or position.cost_usdc is None:
        return Check(False, "no_confirmed_holding")
    w = book.walk_sell(shares)
    if not w.ok:
        return Check(False, "insufficient_bid_depth")
    if w.vwap + EPS < threshold:
        return Check(False, "below_tp")
    sell_fee = fee_usdc(fee_schedule, w.shares, w.vwap)
    if sell_fee is None:
        return Check(False, "fee_schedule_unknown")
    if not (w.usd - sell_fee > position.cost_usdc + 1e-9):
        return Check(False, "not_net_positive")
    return Check(True, "ok", w.vwap, w.limit_price, w.shares)


def norm(text: str | None) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join("".join(c if c.isalnum() else " " for c in t).split())


@dataclass(frozen=True)
class ResultToken:
    """One outcome token of a whole-game result market."""
    token: Token
    market: Market
    kind: str        # HOME | AWAY | DRAW
    is_yes: bool     # soccer YES/NO books; US direct tokens are always True
    label: str

    @property
    def token_id(self) -> str:
        return self.token.token_id


def _team_kind(text: str, game: Game) -> str | None:
    t = norm(text)
    home, away = norm(game.home_team), norm(game.away_team)
    if not t:
        return None
    if t in ("draw", "tie") or t.startswith("draw ") or t.startswith("tie "):
        return "DRAW"
    hit_home = bool(home) and (t == home or home in t or t in home)
    hit_away = bool(away) and (t == away or away in t or t in away)
    if hit_home and not hit_away:
        return "HOME"
    if hit_away and not hit_home:
        return "AWAY"
    return None


def result_tokens(view: MarketView, game: Game, include_no: bool = False) -> list[ResultToken] | None:
    """Whole-game result tokens of a game, or None if the set is not the expected shape.

    Soccer: three neg-risk YES/NO markets (home/draw/away) -> YES tokens (plus NO with
    include_no). US sports: one moneyline with two team tokens.
    The collector has already classified market_type; league/scope filters live there.
    Collector side convention: soccer YES token side = home|away|draw (the market's subject),
    NO token side = 'no'; US team tokens side = home|away.
    """
    markets = view.markets(game.game_key, types=("moneyline", "draw"))
    out: list[ResultToken] = []
    if game.sport == "soccer":
        seen_kinds = set()
        for m in markets:
            if len(m.tokens) != 2:
                return None
            yes_side = next((t.side for t in m.tokens if t.side in ("home", "away", "draw")), None)
            if m.market_type == "draw":
                kind = "DRAW"
            elif yes_side in ("home", "away"):
                kind = yes_side.upper()
            else:
                kind = _team_kind(m.group_item_title or m.question or "", game)
            if kind is None or kind in seen_kinds:
                return None
            seen_kinds.add(kind)
            for t in m.tokens:
                is_yes = t.side != "no" if t.side else t.outcome_index == 0
                if is_yes or include_no:
                    out.append(ResultToken(t, m, kind, is_yes, f"{kind}:{'YES' if is_yes else 'NO'}"))
        if seen_kinds != {"HOME", "DRAW", "AWAY"}:
            return None
        return out
    ml = [m for m in markets if m.market_type == "moneyline"]
    if len(ml) != 1 or len(ml[0].tokens) != 2:
        return None
    for t in ml[0].tokens:
        kind = {"home": "HOME", "away": "AWAY"}.get(t.side or "") or _team_kind(t.outcome_label or "", game)
        if kind not in ("HOME", "AWAY"):
            return None
        out.append(ResultToken(t, ml[0], kind, True, t.outcome_label or kind))
    if {r.kind for r in out} != {"HOME", "AWAY"}:
        return None
    return out


def event_traded(ledger: Ledger, game_key: str | None) -> bool:
    return any(p.game_key == game_key and p.status not in ("unfilled", "failed") for p in ledger.positions)


def floor2(x: float) -> float:
    return int(x * 100 + 1e-9) / 100.0


def game_in_scope(view: MarketView, game: Game, prm: dict) -> bool:
    """Trading scope, narrower than the research collection scope.

    The collector keeps ~45 soccer competitions for calibration research, but real money only goes
    into the legacy-approved leagues (`params.leagues`, soccer only) and games whose result markets
    traded at least `params.min_game_volume_usd` — thin friendlies/qualifiers are excluded.
    """
    leagues = prm.get("leagues")
    if game.sport == "soccer" and leagues and (game.league or "").lower() not in {l.lower() for l in leagues}:
        return False
    floor = prm.get("min_game_volume_usd")
    if floor:
        vol = sum(m.volume or 0.0 for m in view.markets(game.game_key, types=("moneyline", "draw")))
        if vol < float(floor):
            return False
    return True
