from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from polybot.api.gamma_client import GammaClient
from polybot.config import TradingConfig
from polybot.db.models import (
    MarketCatalog,
    MarketSnapshot,
    MarketSweep,
    MarketSweepMembership,
    init_database,
)
from polybot.db.repository import TradeRepository
from polybot.strategy.scanner import MarketScanner, _snapshot_values


def test_snapshot_upserts_replay_catalog(tmp_path):
    Session = init_database(str(tmp_path / "trades.db"))
    session = Session()
    repo = TradeRepository(session)
    market = {
        "id": "123",
        "conditionId": "0xcondition",
        "slug": "market-slug",
        "question": "Will this be replayable?",
        "endDate": "2026-08-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
        "clobTokenIds": ["yes-token", "no-token"],
        "events": [{"id": "event-1", "slug": "event-slug"}],
        "tags": [{"id": "7", "slug": "economics", "label": "Economics"}],
        "feesEnabled": True,
        "feeSchedule": {"rate": 0.05},
        "bestBid": 0.41,
        "bestAsk": 0.43,
        "spread": 0.02,
        "updatedAt": "2026-07-11T00:00:00Z",
    }

    repo.save_snapshot(
        "0xcondition",
        probability=0.42,
        liquidity=20_000,
        volume_24h=4_000,
        best_bid=market["bestBid"],
        best_ask=market["bestAsk"],
        spread=market["spread"],
        source_updated_at=market["updatedAt"],
        market=market,
    )
    market["question"] = "Updated question"
    repo.save_snapshot(
        "0xcondition",
        probability=0.43,
        best_bid=market["bestBid"],
        best_ask=market["bestAsk"],
        spread=market["spread"],
        source_updated_at=market["updatedAt"],
        market=market,
    )

    catalog = session.get(MarketCatalog, "0xcondition")
    assert catalog is not None
    assert catalog.event_id == "event-1"
    assert catalog.event_slug == "event-slug"
    assert catalog.question == "Updated question"
    assert json.loads(catalog.outcomes_json) == ["Yes", "No"]
    assert json.loads(catalog.token_ids_json) == ["yes-token", "no-token"]
    assert json.loads(catalog.tags_json)[0]["slug"] == "economics"
    assert catalog.fees_enabled == 1
    assert catalog.fee_rate == 0.05
    snapshot = repo.get_latest_snapshot("0xcondition")
    assert snapshot.best_bid == 0.41
    assert snapshot.best_ask == 0.43
    assert snapshot.spread == 0.02
    assert snapshot.source_updated_at == "2026-07-11T00:00:00Z"
    assert session.query(MarketCatalog).count() == 1
    session.close()


@pytest.mark.parametrize(
    ("yes_price", "overrides", "expected_reason"),
    [
        (float("inf"), {}, "invalid_yes_price"),
        (1.2, {}, "invalid_yes_price"),
        (0.42, {"liquidity": -1}, "invalid_liquidity"),
        (0.42, {"volume24hr": float("inf")}, "invalid_volume_24h"),
        (0.42, {"bestBid": 1.1}, "invalid_best_bid"),
        (0.42, {"bestAsk": -0.1}, "invalid_best_ask"),
        (0.42, {"spread": -0.01}, "invalid_spread"),
        (
            0.42,
            {"bestBid": 0.6, "bestAsk": 0.5, "spread": 0.1},
            "invalid_order_book",
        ),
        (
            0.42,
            {"bestBid": 0.4, "bestAsk": 0.5, "spread": 0.2},
            "invalid_spread_consistency",
        ),
    ],
)
def test_snapshot_values_reject_invalid_numeric_domain(
    yes_price, overrides, expected_reason
):
    market = {
        "liquidity": 20_000,
        "volume24hr": 1_000,
        "bestBid": 0.4,
        "bestAsk": 0.44,
        "spread": 0.04,
        **overrides,
    }

    values, reason = _snapshot_values(market, yes_price)

    assert values is None
    assert reason == expected_reason


def test_snapshot_values_preserve_valid_zero_book_values():
    values, reason = _snapshot_values(
        {
            "liquidity": 0,
            "volume24hr": 0,
            "bestBid": 0,
            "bestAsk": 0,
            "spread": 0,
        },
        0,
    )

    assert reason == "snapshot_valid"
    assert values == {
        "liquidity": 0.0,
        "volume_24h": 0.0,
        "best_bid": 0.0,
        "best_ask": 0.0,
        "spread": 0.0,
    }


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _KeysetSession:
    def get(self, url, params, timeout):
        return _Response(
            {
                "markets": [
                    {
                        "conditionId": "snapshot-ok",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "20000",
                        "outcomePrices": '["0.42", "0.58"]',
                        "outcomes": '["Yes", "No"]',
                        "clobTokenIds": '["yes", "no"]',
                    },
                    {
                        "conditionId": "no-price",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "20000",
                        "outcomePrices": "[]",
                    },
                    {
                        "conditionId": "invalid-book",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "20000",
                        "outcomePrices": '["0.42", "0.58"]',
                        "bestBid": "0.60",
                        "bestAsk": "0.50",
                        "spread": "0.10",
                    },
                    {
                        "conditionId": "low-liquidity",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "1",
                    },
                ]
            }
        )


def test_complete_sweep_and_snapshots_are_persisted_atomically(tmp_path):
    Session = init_database(str(tmp_path / "sweep.db"))
    session = Session()
    repo = TradeRepository(session)
    gamma = GammaClient()
    gamma.session = _KeysetSession()
    scanner = MarketScanner(gamma, TradingConfig(), repo=repo)

    markets = scanner.fetch_markets()
    assert scanner.save_market_snapshots(markets) == 1

    sweep = session.query(MarketSweep).one()
    assert sweep.cursor_complete == 1
    assert sweep.raw_market_count == 4
    assert sweep.unique_condition_count == 4
    assert sweep.qualified_market_count == 3
    assert sweep.excluded_condition_count == 1
    assert json.loads(sweep.exclusion_counts_json) == {"below_min_liquidity": 1}
    assert sweep.snapshotted_market_count == 1
    memberships = {
        row.condition_id: row
        for row in session.query(MarketSweepMembership).all()
    }
    assert memberships["snapshot-ok"].snapshot_eligible == 1
    assert memberships["snapshot-ok"].snapshotted == 1
    assert memberships["no-price"].qualified == 1
    assert memberships["no-price"].snapshot_eligible == 0
    assert memberships["no-price"].snapshot_reason == "missing_or_invalid_yes_price"
    assert memberships["invalid-book"].snapshot_eligible == 0
    assert memberships["invalid-book"].snapshot_reason == "invalid_order_book"
    assert "low-liquidity" not in memberships
    assert len(memberships) == sweep.qualified_market_count
    assert session.query(MarketSnapshot).count() == 1
    assert {
        row.condition_id for row in session.query(MarketCatalog).all()
    } == {"snapshot-ok", "no-price", "invalid-book"}
    sweep.completed_at = datetime.utcnow() - timedelta(days=61)
    session.commit()
    repo.cleanup_old_snapshots(days=60)
    assert session.query(MarketSweep).count() == 0
    assert session.query(MarketSweepMembership).count() == 0
    assert session.query(MarketSnapshot).count() == 1
    session.close()
