"""Builders for tmp core/books/strategy DBs used by strategy, engine and backtest tests.

(No tests here; named test_* only so it sits inside the strategy test namespace.)
"""

from __future__ import annotations

import json

from polylab import db
from polylab.marketview import MarketView, encode_levels
from polylab.settings import Paths

T0 = 1_790_000_000 - (1_790_000_000 % 3600)     # 2026-09-21-ish, hour aligned


class Env:
    def __init__(self, root):
        self.paths = Paths(root).ensure()
        self.core = db.core(self.paths)
        self._books = {}

    def books_conn(self, ts: int):
        ym = db.year_month(ts)
        if ym not in self._books:
            self._books[ym] = db.books(self.paths, ym)
        return self._books[ym]

    def game(self, key, sport, start, home="Home FC", away="Away FC", status="live", ended_at=None,
             league=None, first_seen=None):
        self.core.execute(
            "INSERT OR REPLACE INTO games(game_key, sport, league, title, home_team, away_team, start_time, status, "
            "ended_at, first_seen, updated_at, source) VALUES(?,?,?,?,?,?,?,?,?,?,?, 'discover')",
            (key, sport, league, f"{home} vs {away}", home, away, start, status, ended_at,
             first_seen if first_seen is not None else T0 - 7 * 86400, start))
        self.core.commit()

    def market(self, cond, game_key, mtype, tokens, group=None, liquidity=200000.0, volume=50000.0,
               fee_schedule=None, resolved=None, resolved_at=None, closed=0):
        self.core.execute(
            "INSERT OR REPLACE INTO markets(condition_id, game_key, market_type, question, group_item_title, volume, "
            "liquidity, fee_schedule, neg_risk, closed, resolved_outcome_index, resolved_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (cond, game_key, mtype, group, group, volume, liquidity,
             json.dumps(fee_schedule) if fee_schedule is not None else None, 0, closed, resolved, resolved_at))
        for i, (tid, label, side) in enumerate(tokens):
            self.core.execute("INSERT OR REPLACE INTO tokens(token_id, condition_id, outcome_index, outcome_label, side) "
                              "VALUES(?,?,?,?,?)", (tid, cond, i, label, side))
        self.core.commit()

    def resolve(self, cond, idx, at):
        self.core.execute("UPDATE markets SET resolved_outcome_index=?, resolved_at=?, closed=1 WHERE condition_id=?",
                          (idx, at, cond))
        self.core.commit()

    def book(self, token, ts, bids, asks):
        conn = self.books_conn(ts)
        bb = max((p for p, _ in bids), default=None)
        ba = min((p for p, _ in asks), default=None)
        conn.execute(
            "INSERT OR REPLACE INTO book_snapshots(token_id, ts, source, best_bid, best_ask, mid, spread, "
            "bid_depth_usd, ask_depth_usd, levels_z) VALUES(?,?, 'poll', ?,?,?,?,?,?,?)",
            (token, ts, bb, ba, (bb + ba) / 2 if bb and ba else None, (ba - bb) if bb and ba else None,
             sum(p * s for p, s in bids), sum(p * s for p, s in asks), encode_levels(bids, asks)))
        conn.commit()

    def bar(self, token, ts, price, source="poll_mid"):
        self.core.execute("INSERT OR REPLACE INTO price_bars(token_id, ts, source, price) VALUES(?,?,?,?)",
                          (token, ts - ts % 60, source, price))
        self.core.commit()

    def state(self, game_key, ts, period, elapsed, minute=None, live=1):
        self.core.execute(
            "INSERT OR REPLACE INTO game_states(game_key, ts, received_at, source, status, live, period, elapsed, "
            "game_minute) VALUES(?,?,?, 'ws_sports', 'live', ?,?,?,?)",
            (game_key, ts, ts, live, period, elapsed, minute))
        self.core.commit()

    def view(self, now, **kw) -> MarketView:
        return MarketView.open(self.paths, now, **kw)

    # -- common shapes
    def us_game(self, key, sport, start, cond=None, t_home=None, t_away=None, **kw):
        cond = cond or f"c-{key}"
        self.game(key, sport, start, home="Yankees", away="Red Sox", **kw)
        self.market(cond, key, "moneyline", [(t_home or f"{key}-h", "Yankees", "home"),
                                             (t_away or f"{key}-a", "Red Sox", "away")])
        return cond

    def soccer_game(self, key, start, **kw):
        self.game(key, "soccer", start, home="Arsenal", away="Chelsea", **kw)
        self.market(f"c-{key}-H", key, "moneyline", [(f"{key}-H-Y", "Yes", "home"), (f"{key}-H-N", "No", "no")],
                    group="Arsenal")
        self.market(f"c-{key}-D", key, "draw", [(f"{key}-D-Y", "Yes", "draw"), (f"{key}-D-N", "No", "no")],
                    group="Draw (Arsenal vs. Chelsea)")
        self.market(f"c-{key}-A", key, "moneyline", [(f"{key}-A-Y", "Yes", "away"), (f"{key}-A-N", "No", "no")],
                    group="Chelsea FC")


def level_book(env: Env, token, ts, bid, ask, size=1000.0):
    env.book(token, ts, [(bid, size)], [(ask, size)])
