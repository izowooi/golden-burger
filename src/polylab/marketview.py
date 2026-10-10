"""Read-only, as-of-`now` view over core.db + books shards for strategies.

Every query is bounded by `now` so the same view drives the live engine and the
backtest replay. "Live" is derived from time (start/ended_at/max age), not from
`games.status`, which only reflects the collector's current knowledge.

Book walks live here too so the paper broker, engine re-check, strategies and
backtest all share one definition of "exact-$X VWAP".
"""

from __future__ import annotations

import json
import sqlite3
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

PRICE_SOURCE_PRIORITY = {"poll_mid": 0, "ws_last": 1, "history": 2}
DEFAULT_MAX_IN_PLAY_HOURS = {"soccer": 4.0, "mlb": 8.0, "nba": 5.0, "nfl": 6.0, "nhl": 5.0}
EPS = 1e-9


# ---------------------------------------------------------------- book walks

@dataclass(frozen=True)
class Walk:
    ok: bool                    # full depth available
    vwap: float | None
    limit_price: float | None   # worst level touched
    shares: float               # BUY: shares bought / SELL: shares sold
    usd: float                  # BUY: spent / SELL: proceeds


@dataclass
class Book:
    token_id: str
    ts: int
    bids: list[tuple[float, float]]   # descending price
    asks: list[tuple[float, float]]   # ascending price
    source: str = "book"
    synthetic: bool = False

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def crossed(self) -> bool:
        return (self.best_bid is not None and self.best_ask is not None
                and self.best_bid > self.best_ask + EPS)

    def walk_buy(self, usd: float) -> Walk:
        return walk_asks(self.asks, usd)

    def walk_sell(self, shares: float) -> Walk:
        return walk_bids(self.bids, shares)


def _clean_levels(levels: Iterable, descending: bool) -> list[tuple[float, float]]:
    out = []
    for lvl in levels or []:
        if isinstance(lvl, dict):
            p, s = lvl.get("price"), lvl.get("size")
        else:
            p, s = lvl[0], lvl[1]
        p, s = float(p), float(s)
        if 0 < p <= 1 and s > 0:
            out.append((p, s))
    out.sort(key=lambda x: x[0], reverse=descending)
    return out


def make_book(token_id: str, ts: int, bids: Iterable, asks: Iterable, source: str = "book") -> Book:
    return Book(token_id, int(ts), _clean_levels(bids, True), _clean_levels(asks, False), source)


def walk_asks(asks: list[tuple[float, float]], usd: float) -> Walk:
    """Spend exactly `usd` walking asks best-first; missing depth is never imputed."""
    remaining, shares, limit = float(usd), 0.0, None
    for price, size in asks:
        spent = min(remaining, price * size)
        shares += spent / price
        remaining -= spent
        limit = price
        if remaining <= EPS:
            break
    if remaining > 1e-7 or shares <= 0:
        return Walk(False, None, limit, shares, usd - remaining)
    return Walk(True, usd / shares, limit, shares, usd)


def walk_bids(bids: list[tuple[float, float]], shares: float) -> Walk:
    remaining, proceeds, limit = float(shares), 0.0, None
    for price, size in bids:
        sold = min(remaining, size)
        proceeds += sold * price
        remaining -= sold
        limit = price
        if remaining <= EPS:
            break
    if remaining > 1e-7 or shares <= 0:
        return Walk(False, None, limit, shares - remaining, proceeds)
    return Walk(True, proceeds / shares, limit, shares, proceeds)


def decode_levels(blob: bytes | None) -> tuple[list, list]:
    if not blob:
        return [], []
    data = json.loads(zlib.decompress(blob))
    return data.get("b") or [], data.get("a") or []


def encode_levels(bids: list, asks: list) -> bytes:
    return zlib.compress(json.dumps({"b": bids, "a": asks}).encode())


# ---------------------------------------------------------------- core rows

@dataclass(frozen=True)
class Game:
    game_key: str
    sport: str
    league: str | None
    title: str | None
    home_team: str | None
    away_team: str | None
    start_time: int | None
    status: str | None
    ended_at: int | None
    first_seen: int | None


@dataclass(frozen=True)
class Token:
    token_id: str
    condition_id: str
    outcome_index: int
    outcome_label: str | None
    side: str | None


@dataclass(frozen=True)
class Market:
    condition_id: str
    game_key: str | None
    market_type: str
    question: str | None
    group_item_title: str | None
    line: float | None
    volume: float | None
    liquidity: float | None
    fee_schedule: str | None
    neg_risk: bool
    closed: bool
    resolved_outcome_index: int | None
    resolved_at: int | None
    tokens: tuple[Token, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GameState:
    game_key: str
    ts: int
    status: str | None
    live: bool | None
    ended: bool | None
    period: str | None
    elapsed: str | None
    game_minute: float | None
    home_score: int | None
    away_score: int | None


def _row_game(r: sqlite3.Row) -> Game:
    return Game(r["game_key"], r["sport"], r["league"], r["title"], r["home_team"], r["away_team"],
                r["start_time"], r["status"], r["ended_at"], r["first_seen"])


class MarketView:
    """Read-only as-of view. `historical=True` ignores current status flags (backtest)."""

    def __init__(self, core: sqlite3.Connection, books: list[sqlite3.Connection] | None = None,
                 historical: bool = False, synthetic_spread: float | None = None,
                 synthetic_depth_usd: float = 1e6, general=None):
        self.core = core
        self.books = books or []
        self.historical = historical
        self.synthetic_spread = synthetic_spread
        self.synthetic_depth_usd = synthetic_depth_usd
        self._markets_cache: dict[str, list[Market]] = {}
        # data/general (polylab.general.view.GeneralView): consulted only on a core.db miss (cherry's markets)
        self.general = general

    # -- construction
    @classmethod
    def open(cls, paths, now: int | None = None, **kw) -> "MarketView":
        now = int(now or time.time())
        core = _ro(paths.core_db)
        if core is None:
            raise FileNotFoundError(f"core db missing: {paths.core_db}")
        if "general" not in kw:
            from polylab.general.view import GeneralView  # noqa: PLC0415
            kw["general"] = GeneralView.open(paths, now, historical=bool(kw.get("historical")))
        return cls(core, _open_book_shards(paths, now), **kw)

    def close(self) -> None:
        for c in [self.core, *self.books]:
            try:
                c.close()
            except Exception:
                pass
        if self.general is not None:
            self.general.close()

    # -- games
    def game(self, game_key: str) -> Game | None:
        r = self.core.execute("SELECT * FROM games WHERE game_key=?", (game_key,)).fetchone()
        return _row_game(r) if r else None

    def _is_over(self, g: Game, now: int) -> bool:
        if g.status == "cancelled":
            return True
        if g.ended_at is not None and g.ended_at <= now:
            return True
        if not self.historical and g.ended_at is None and g.status in ("ended", "final", "closed"):
            return True
        return False

    def game_over(self, g: Game, now: int) -> bool:
        """Cancelled, ended (ended_at <= now) or — live view only — flagged ended/final/closed."""
        return self._is_over(g, now)

    def live_games(self, now: int, sports: Iterable[str] | None = None,
                   max_age_hours: dict[str, float] | float | None = None) -> list[Game]:
        """Games that started at or before now, within the per-sport max age and not over."""
        sports = list(sports) if sports else list(DEFAULT_MAX_IN_PLAY_HOURS)
        widest = 24 * 3600
        rows = self.core.execute(
            f"SELECT * FROM games WHERE sport IN ({','.join('?' * len(sports))}) "
            "AND start_time IS NOT NULL AND start_time <= ? AND start_time >= ? ORDER BY start_time, game_key",
            (*sports, now, now - widest)).fetchall()
        out = []
        for r in rows:
            g = _row_game(r)
            if isinstance(max_age_hours, (int, float)):
                limit_h = float(max_age_hours)
            else:
                limit_h = (max_age_hours or {}).get(g.sport, DEFAULT_MAX_IN_PLAY_HOURS.get(g.sport, 6.0))
            if now - g.start_time > limit_h * 3600 or self._is_over(g, now):
                continue
            out.append(g)
        return out

    def upcoming_games(self, now: int, within_hours: float, sports: Iterable[str] | None = None) -> list[Game]:
        sports = list(sports) if sports else list(DEFAULT_MAX_IN_PLAY_HOURS)
        rows = self.core.execute(
            f"SELECT * FROM games WHERE sport IN ({','.join('?' * len(sports))}) "
            "AND start_time > ? AND start_time <= ? ORDER BY start_time, game_key",
            (*sports, now, now + int(within_hours * 3600))).fetchall()
        games = [_row_game(r) for r in rows]
        # a game has to be known (discovered) at `now` to be tradable in a replay
        return [g for g in games if not self._is_over(g, now) and (g.first_seen is None or g.first_seen <= now)]

    # -- markets
    def markets(self, game_key: str, types: Iterable[str] | None = None) -> list[Market]:
        if game_key not in self._markets_cache:
            rows = self.core.execute(
                "SELECT * FROM markets WHERE game_key=? ORDER BY condition_id", (game_key,)).fetchall()
            out = []
            for r in rows:
                toks = self.core.execute(
                    "SELECT * FROM tokens WHERE condition_id=? ORDER BY outcome_index", (r["condition_id"],)).fetchall()
                out.append(Market(
                    r["condition_id"], r["game_key"], r["market_type"], r["question"], r["group_item_title"],
                    r["line"], r["volume"], r["liquidity"], r["fee_schedule"], bool(r["neg_risk"]),
                    bool(r["closed"]), r["resolved_outcome_index"], r["resolved_at"],
                    tuple(Token(t["token_id"], t["condition_id"], t["outcome_index"], t["outcome_label"], t["side"])
                          for t in toks)))
            self._markets_cache[game_key] = out
        ms = self._markets_cache[game_key]
        if types is not None:
            types = set(types)
            ms = [m for m in ms if m.market_type in types]
        # moneyline first, then draw, then the rest
        order = {"moneyline": 0, "draw": 1}
        return sorted(ms, key=lambda m: (order.get(m.market_type, 2), m.condition_id))

    def market(self, condition_id: str) -> Market | None:
        r = self.core.execute("SELECT game_key FROM markets WHERE condition_id=?", (condition_id,)).fetchone()
        if not r:
            return self.general_market(condition_id)
        if r["game_key"] is None:
            return None
        for m in self.markets(r["game_key"]):
            if m.condition_id == condition_id:
                return m
        return None

    def token_market(self, token_id: str) -> Market | None:
        r = self.core.execute("SELECT condition_id FROM tokens WHERE token_id=?", (token_id,)).fetchone()
        if r is None and self.general is not None:
            t = self.general.token(token_id)
            if t is not None:
                g = self.general.registry.execute("SELECT condition_id FROM gen_markets WHERE id=?", (t[0],)).fetchone()
                return self.general_market(g[0]) if g else None
        return self.market(r["condition_id"]) if r else None

    def general_market(self, condition_id: str) -> Market | None:
        """A data/general market as a core-shaped Market (game_key None, market_type 'general'), core miss only."""
        g = self.general.market(condition_id) if self.general is not None else None
        if g is None:
            return None
        labels = list(g.outcomes) + [None, None]
        return Market(g.condition_id, None, "general", g.question, None, None, g.volume, g.liquidity, g.fee_json,
                      g.neg_risk, g.closed, g.resolved_index, g.closed_at,
                      (Token(g.yes_token, g.condition_id, 0, labels[0], "yes"),
                       Token(g.no_token, g.condition_id, 1, labels[1], "no")))

    def resolution(self, condition_id: str, now: int) -> int | None:
        """Winning outcome index if the market was resolved at or before `now`."""
        r = self.core.execute(
            "SELECT resolved_outcome_index, resolved_at FROM markets WHERE condition_id=?", (condition_id,)).fetchone()
        if r is None and self.general is not None:
            return self.general.resolution(condition_id, now)
        if not r or r["resolved_outcome_index"] is None:
            return None
        if r["resolved_at"] is None:
            return None if self.historical else int(r["resolved_outcome_index"])
        return int(r["resolved_outcome_index"]) if r["resolved_at"] <= now else None

    def is_tradable(self, market: Market, now: int) -> bool:
        if self.resolution(market.condition_id, now) is not None:
            return False
        return self.historical or not market.closed

    # -- prices
    def price(self, token_id: str, now: int, max_age_s: int | None = None) -> tuple[int, float] | None:
        """Latest canonical 1-minute price at or before now: (ts, price)."""
        rows = self.core.execute(
            "SELECT ts, source, price FROM price_bars WHERE token_id=? AND ts<=? ORDER BY ts DESC LIMIT 6",
            (token_id, now)).fetchall()
        if not rows:
            return self.general.price(token_id, now, max_age_s) if self.general is not None else None
        top = rows[0]["ts"]
        best = min((r for r in rows if r["ts"] == top), key=lambda r: PRICE_SOURCE_PRIORITY.get(r["source"], 9))
        if max_age_s is not None and now - top > max_age_s:
            return None
        return int(top), float(best["price"])

    def price_bars(self, token_id: str, since: int, until: int) -> list[tuple[int, float]]:
        rows = self.core.execute(
            "SELECT ts, source, price FROM price_bars WHERE token_id=? AND ts>=? AND ts<=? ORDER BY ts",
            (token_id, since, until)).fetchall()
        if not rows and self.general is not None:
            return self.general.price_bars(token_id, since, until)
        best: dict[int, tuple[int, float]] = {}
        for r in rows:
            pri = PRICE_SOURCE_PRIORITY.get(r["source"], 9)
            if r["ts"] not in best or pri < best[r["ts"]][0]:
                best[r["ts"]] = (pri, float(r["price"]))
        return [(ts, v[1]) for ts, v in sorted(best.items())]

    # -- books
    def book(self, token_id: str, now: int, max_age_s: int | None = 180) -> Book | None:
        """Latest stored book at or before now (None if absent or stale).

        With `synthetic_spread` set (backtest only), a missing book is synthesised from the
        canonical price as price ± spread/2 with deep levels.
        """
        best = None
        for conn in self.books:
            r = conn.execute(
                "SELECT ts, source, levels_z FROM book_snapshots WHERE token_id=? AND ts<=? "
                "ORDER BY ts DESC LIMIT 1", (token_id, now)).fetchone()
            if r and (best is None or r["ts"] > best["ts"]):
                best = r
        if best is not None and (max_age_s is None or now - best["ts"] <= max_age_s):
            bids, asks = decode_levels(best["levels_z"])
            return make_book(token_id, best["ts"], bids, asks, best["source"])
        if best is None and self.general is not None:
            l1 = self.general.l1(token_id, now, max_age_s)
            if l1 is not None:
                return make_book(token_id, l1[0], l1[1], l1[2], "general_l1")
        if self.synthetic_spread is None:
            return None
        p = self.price(token_id, now, max_age_s=max_age_s if max_age_s is not None else None)
        if p is None:
            return None
        return synthetic_book(token_id, p[0], p[1], self.synthetic_spread, self.synthetic_depth_usd)

    def tick0(self, game: Game, token_ids: list[str], now: int, min_depth_usd: float = 5.0) -> int | None:
        """First minute (>= start) in which every token had a book with >= $X ask depth.

        Apricot's clock: the first cycle both teams had executable books while live. When no
        book at all exists for these tokens (history-only data) fall back to the first minute
        where every token has a price bar.
        """
        start = game.start_time or 0
        per_token: list[set[int]] = []
        any_book = False
        for tok in token_ids:
            minutes: set[int] = set()
            for conn in self.books:
                for r in conn.execute(
                        "SELECT ts, ask_depth_usd FROM book_snapshots WHERE token_id=? AND ts>=? AND ts<=?",
                        (tok, start, now)):
                    any_book = True
                    if (r["ask_depth_usd"] or 0) + EPS >= min_depth_usd:
                        minutes.add(r["ts"] // 60)
            per_token.append(minutes)
        if not any_book:
            per_token = [{ts // 60 for ts, _ in self.price_bars(t, start, now)} for t in token_ids]
        if not per_token:
            return None
        common = set.intersection(*per_token)
        return min(common) * 60 if common else None

    # -- game state
    def game_state(self, game_key: str, now: int) -> GameState | None:
        r = self.core.execute(
            "SELECT * FROM game_states WHERE game_key=? AND ts<=? ORDER BY ts DESC, source LIMIT 1",
            (game_key, now)).fetchone()
        if not r:
            return None
        return GameState(r["game_key"], r["ts"], r["status"],
                         None if r["live"] is None else bool(r["live"]),
                         None if r["ended"] is None else bool(r["ended"]),
                         r["period"], r["elapsed"], r["game_minute"], r["home_score"], r["away_score"])

    def elapsed_since_start(self, game: Game, now: int) -> float | None:
        """Minutes since scheduled start (wall clock)."""
        if game.start_time is None:
            return None
        return (now - game.start_time) / 60.0


def synthetic_book(token_id: str, ts: int, price: float, spread: float, depth_usd: float = 1e6) -> Book:
    bid = max(0.001, round(price - spread / 2, 6))
    ask = min(0.999, round(price + spread / 2, 6))
    return Book(token_id, ts, [(bid, depth_usd / bid)], [(ask, depth_usd / ask)], "synthetic", True)


def _ro(path: Path) -> sqlite3.Connection | None:
    if not Path(path).exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def _open_book_shards(paths, now: int, months_back: int = 1) -> list[sqlite3.Connection]:
    conns = []
    t = time.gmtime(now)
    y, m = t.tm_year, t.tm_mon
    for _ in range(months_back + 1):
        c = _ro(paths.books_db(f"{y:04d}-{m:02d}"))
        if c is not None:
            conns.append(c)
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return conns


def book_shards_for_range(paths, start: int, end: int) -> list[sqlite3.Connection]:
    conns, seen = [], set()
    t = start - 31 * 86400
    while t <= end + 31 * 86400:
        ym = time.strftime("%Y-%m", time.gmtime(t))
        if ym not in seen:
            seen.add(ym)
            c = _ro(paths.books_db(ym))
            if c is not None:
                conns.append(c)
        t += 20 * 86400
    return conns
