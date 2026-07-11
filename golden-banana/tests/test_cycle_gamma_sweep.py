"""Regression coverage for sharing one Gamma universe within a cycle."""

from types import SimpleNamespace

import polybot.bot as bot_module
from polybot.bot import PolymarketBot
from polybot.strategy.scanner import MarketScanner


class FakeGamma:
    def __init__(self):
        self.calls = []

    def get_all_tradable_markets(self, min_liquidity=0, min_volume=0):
        self.calls.append((min_liquidity, min_volume))
        return []


class FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeRepository:
    def get_holding_trades(self):
        return []

    def cleanup_old_snapshots(self, days):
        assert days == 7
        return 0

    def get_stats(self):
        return {"holding": 0, "total_pnl": 0.0}


class FakeTrader:
    def __init__(self, *_args, **_kwargs):
        pass


def test_cycle_shares_one_gamma_sweep_between_snapshot_and_scan(monkeypatch):
    session = FakeSession()
    repository = FakeRepository()
    gamma = FakeGamma()
    monkeypatch.setattr(bot_module, "TradeRepository", lambda _session: repository)
    monkeypatch.setattr(bot_module, "Trader", FakeTrader)

    bot = PolymarketBot.__new__(PolymarketBot)
    bot.Session = lambda: session
    bot.gamma = gamma
    bot.clob = object()
    bot.config = SimpleNamespace(
        trading=SimpleNamespace(
            min_liquidity=50_000,
            momentum=SimpleNamespace(enabled=False),
        )
    )

    stats = bot.run_cycle()

    assert gamma.calls == [(50_000, 0)]
    assert stats == {
        "snapshots_saved": 0,
        "checked_holdings": 0,
        "sold": 0,
        "buy_candidates": 0,
        "bought": 0,
    }
    assert session.closed is True


def test_snapshot_rows_commit_once_per_cycle():
    class SnapshotRepository:
        def __init__(self):
            self.rows = []
            self.commits = 0

        def save_snapshot(self, **kwargs):
            self.rows.append(kwargs)

        def commit(self):
            self.commits += 1

        def rollback(self):
            raise AssertionError("rollback is not expected")

    repository = SnapshotRepository()
    scanner = MarketScanner(
        FakeGamma(),
        SimpleNamespace(
            excluded_categories=[],
            min_liquidity=50_000,
            momentum=SimpleNamespace(enabled=False),
        ),
        repository,
    )
    markets = [
        {
            "conditionId": f"condition-{index}",
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.9", "0.1"],
            "clobTokenIds": [f"yes-{index}", f"no-{index}"],
            "liquidity": "50000",
            "volume24hr": "1000",
        }
        for index in range(2)
    ]

    assert scanner.save_market_snapshots(markets) == 2
    assert repository.commits == 1
    assert all(row["commit"] is False for row in repository.rows)
