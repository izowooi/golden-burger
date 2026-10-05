"""Read-only, as-of view over data/general for MarketView's core-miss fallback and cherry's universe.

MarketView consults this only when core.db has no answer (no market row / no price bar / no book), so every
core-backed result stays exactly as before. Binary books mirror: the NO side is derived from the YES row
(NO bid = 1 - YES ask, NO ask = 1 - YES bid, NO p = 1 - YES p).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from polylab.general import store


@dataclass(frozen=True)
class GeneralMarket:
    gid: int
    condition_id: str
    question: str | None
    category: str
    in_core: bool
    event_id: str | None
    neg_risk: bool
    yes_token: str
    no_token: str
    outcomes: tuple[str, ...]
    listed_at: int | None           # Gamma startDate (else createdAt)
    end_ref: int
    volume: float | None
    liquidity: float | None
    fee_json: str | None
    closed: bool
    closed_at: int | None
    resolved_index: int | None


def _ro(path: Path) -> sqlite3.Connection | None:
    if not Path(path).exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def _row_market(r: sqlite3.Row) -> GeneralMarket:
    try:
        outcomes = tuple(str(x) for x in json.loads(r["outcomes"] or "[]"))
    except (TypeError, ValueError):
        outcomes = ()
    return GeneralMarket(int(r["id"]), r["condition_id"], r["question"], r["category"] or "other",
                         bool(r["in_core"]), r["event_id"], bool(r["neg_risk"]), r["yes_token"], r["no_token"],
                         outcomes, r["start_date"] or r["created_at"], int(r["end_ref"]), r["volume"],
                         r["liquidity"], r["fee_json"], bool(r["closed"]), r["closed_at"], r["resolved_index"])


class GeneralView:
    def __init__(self, registry: sqlite3.Connection, shards: list[sqlite3.Connection], historical: bool = False):
        self.registry = registry
        self.shards = shards
        self.historical = historical
        self._tok: dict[str, tuple[int, bool] | None] = {}
        self._cid: dict[str, GeneralMarket | None] = {}

    @classmethod
    def open(cls, paths, now: int | None = None, *, start: int | None = None, end: int | None = None,
             historical: bool = False) -> "GeneralView | None":
        reg = _ro(store.registry_path(paths))
        if reg is None:
            return None
        now = int(now or time.time())
        lo, hi = (start, end) if start is not None and end is not None else (now - 40 * 86400, now)
        months: list[str] = []
        t = lo - 5 * 86400
        while t <= hi + 5 * 86400:
            ym = time.strftime("%Y-%m", time.gmtime(t))
            if ym not in months:
                months.append(ym)
            t += 10 * 86400
        shards = [c for c in (_ro(store.general_dir(paths) / f"{ym}.db") for ym in months) if c is not None]
        return cls(reg, shards, historical)

    def close(self) -> None:
        for c in [self.registry, *self.shards]:
            try:
                c.close()
            except Exception:
                pass

    # -- registry
    def token(self, token_id: str) -> tuple[int, bool] | None:
        """(gid, is_yes) of a registered token."""
        if token_id not in self._tok:
            r = self.registry.execute("SELECT id FROM gen_markets WHERE yes_token=?", (token_id,)).fetchone()
            if r is not None:
                self._tok[token_id] = (int(r[0]), True)
            else:
                r = self.registry.execute("SELECT id FROM gen_markets WHERE no_token=?", (token_id,)).fetchone()
                self._tok[token_id] = (int(r[0]), False) if r is not None else None
        return self._tok[token_id]

    def market(self, condition_id: str) -> GeneralMarket | None:
        if condition_id not in self._cid:
            r = self.registry.execute("SELECT * FROM gen_markets WHERE condition_id=? AND end_ref IS NOT NULL",
                                      (condition_id,)).fetchone()
            self._cid[condition_id] = _row_market(r) if r is not None else None
        return self._cid[condition_id]

    def candidates(self, now: int, hours_min: float, hours_max: float) -> list[GeneralMarket]:
        """Markets whose end_ref is in [now + hours_min, now + hours_max], listed at `now`, unresolved at `now`."""
        rows = self.registry.execute(
            "SELECT * FROM gen_markets WHERE end_ref BETWEEN ? AND ? ORDER BY end_ref, id",
            (now + int(hours_min * 3600), now + int(hours_max * 3600))).fetchall()
        out = []
        for r in rows:
            m = _row_market(r)
            if m.listed_at is not None and m.listed_at > now:
                continue
            if self.historical:
                if m.closed_at is not None and m.closed_at <= now:
                    continue
            elif m.closed:
                continue
            out.append(m)
        return out

    def volume_asof(self, m: GeneralMarket, now: int) -> tuple[float | None, float | None]:
        """(volume, liquidity) from the latest discover metrics row at or before now, else the registry value
        (for history-only markets that is the FINAL volume: look-ahead, documented in the backtest)."""
        r = self.registry.execute("SELECT volume, liquidity FROM gen_metrics WHERE id=? AND ts<=? ORDER BY ts DESC "
                                  "LIMIT 1", (m.gid, now)).fetchone()
        if r is not None:
            return r["volume"], r["liquidity"]
        return m.volume, m.liquidity

    def resolution(self, condition_id: str, now: int) -> int | None:
        m = self.market(condition_id)
        if m is None or m.resolved_index is None:
            return None
        if m.closed_at is None:
            return None if self.historical else int(m.resolved_index)
        return int(m.resolved_index) if m.closed_at <= now else None

    # -- quotes
    def _latest(self, gid: int, now: int, poll_only: bool = False) -> sqlite3.Row | None:
        best = None
        sql = ("SELECT ts, bid, ask, bid_sz, ask_sz, last, p, src FROM gen_quotes WHERE id=? AND ts<=? "
               + ("AND src=0 " if poll_only else "") + "ORDER BY ts DESC LIMIT 1")
        for c in self.shards:
            r = c.execute(sql, (gid, now)).fetchone()
            if r is not None and (best is None or r["ts"] > best["ts"]):
                best = r
        return best

    def price(self, token_id: str, now: int, max_age_s: int | None = None) -> tuple[int, float] | None:
        t = self.token(token_id)
        if t is None:
            return None
        r = self._latest(t[0], now)
        if r is None or r["p"] is None or (max_age_s is not None and now - r["ts"] > max_age_s):
            return None
        return int(r["ts"]), float(r["p"]) if t[1] else round(1.0 - float(r["p"]), 6)

    def price_bars(self, token_id: str, since: int, until: int) -> list[tuple[int, float]]:
        t = self.token(token_id)
        if t is None:
            return []
        out: dict[int, float] = {}
        for c in self.shards:
            for r in c.execute("SELECT ts, p FROM gen_quotes WHERE id=? AND ts>=? AND ts<=? AND p IS NOT NULL",
                               (t[0], since, until)):
                out[int(r["ts"])] = float(r["p"]) if t[1] else round(1.0 - float(r["p"]), 6)
        return sorted(out.items())

    def l1(self, token_id: str, now: int, max_age_s: int | None = 180):
        """(ts, bids, asks) level-1 of the token from the latest polled row, mirrored for NO; None if stale/absent."""
        t = self.token(token_id)
        if t is None:
            return None
        r = self._latest(t[0], now, poll_only=True)
        if r is None or (max_age_s is not None and now - r["ts"] > max_age_s):
            return None
        bid, ask, bsz, asz = r["bid"], r["ask"], r["bid_sz"], r["ask_sz"]
        if t[1]:
            bids = [(bid, bsz)] if bid is not None and bsz else []
            asks = [(ask, asz)] if ask is not None and asz else []
        else:
            bids = [(round(1.0 - ask, 6), asz)] if ask is not None and asz else []
            asks = [(round(1.0 - bid, 6), bsz)] if bid is not None and bsz else []
        return int(r["ts"]), bids, asks
