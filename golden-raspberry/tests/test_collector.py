from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3

from polybot.api.clob_client import BookAttempt, BookCollection
from polybot.api.gamma_client import GammaPage, GammaSweep
from polybot.collector import ResearchCollector
from polybot.config import PROJECT_ROOT, load_config
from polybot.db.repository import ResearchRepository


def _condition_for_shard(shard: int) -> str:
    for index in range(10_000):
        value = f"condition-{index}"
        if int(hashlib.sha256(value.encode()).hexdigest(), 16) % 3 == shard:
            return value
    raise AssertionError


class FakeGamma:
    def __init__(self, market):
        self.market = market

    def collect_market_sweep(self, run_id):
        return GammaSweep(
            pages=[GammaPage(1, "2026-08-13T01:00:00Z", "gamma", None, None, [self.market])],
            cursor_complete=True,
        )


class FakeClob:
    def __init__(self, books):
        self.books = books

    def fetch_books(self, run_id, token_ids, *, atomic_pairs=None):
        assert atomic_pairs == [("yes", "no")]
        attempts = {
            token: BookAttempt(
                token, "OBSERVED", None, "2026-08-13T01:00:01Z", "2026-08-13T01:00:02Z"
            )
            for token in token_ids
        }
        return BookCollection(
            books={token: self.books[token] for token in token_ids},
            attempts=attempts,
            raw_payloads=[],
        )


def _book(token, bid_size, ask_size):
    return {
        "asset_id": token,
        "market": "market",
        "timestamp": "1",
        "hash": token,
        "tick_size": "0.01",
        "min_order_size": "5",
        "bids": [
            {"price": "0.49", "size": str(bid_size)},
            {"price": "0.48", "size": str(bid_size)},
            {"price": "0.47", "size": str(bid_size)},
        ],
        "asks": [
            {"price": "0.50", "size": str(ask_size)},
            {"price": "0.51", "size": str(ask_size)},
            {"price": "0.52", "size": str(ask_size)},
        ],
    }


def test_one_cycle_publishes_pair_and_all_three_derived_arms(monkeypatch, tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-shard-0")
    object.__setattr__(config, "db_path", tmp_path / "trades_sim.db")
    repository = ResearchRepository(config.db_path)
    repository.initialize(config)
    condition = _condition_for_shard(0)
    market = {
        "id": "market",
        "conditionId": condition,
        "events": [{"id": "event"}],
        "question": "test",
        "slug": "test",
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps(["yes", "no"]),
        "outcomePrices": json.dumps(["0.5", "0.5"]),
        "endDate": "2026-08-20T00:00:00Z",
        "liquidityNum": 50_000,
        "volumeNum": 100_000,
        "volume24hr": 20_000,
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "negRisk": False,
    }
    collector = ResearchCollector(
        config,
        repository=repository,
        gamma_client=FakeGamma(market),
        clob_client=FakeClob({"yes": _book("yes", 1000, 100), "no": _book("no", 100, 1000)}),
    )
    stats = collector.run_cycle("run")
    assert stats["shard_markets"] == 1
    with sqlite3.connect(repository.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM market_sweeps").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0] == 2
        arms = connection.execute(
            """
            SELECT arm, one_sided_candidate, prior_move_bin
            FROM signal_decisions ORDER BY confirmation_steps
            """
        ).fetchall()
    assert arms == [
        ("DO", 1, "MISSING"),
        ("RE", 1, "MISSING"),
        ("MI", 1, "MISSING"),
    ]
