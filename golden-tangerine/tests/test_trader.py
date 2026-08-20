from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import polybot.strategy.trader as trader_module
from polybot.api.clob_client import BuyBookWalk
from polybot.config import TradingConfig
from polybot.db.models import TradeStatus
from polybot.db.repository import ExactFillEvidence
from polybot.strategy.trader import Trader


NOW = datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return NOW.replace(tzinfo=None)


class _Repo:
    def __init__(self):
        self.created = []
        self.updated = []
        self.linked = []

    def can_reenter(self, *_args):
        return True, "ok"

    def get_position_count(self):
        return 0

    def get_event_position_count(self, _event_id):
        return 0

    def create_trade(self, **values):
        self.created.append(values)
        return SimpleNamespace(id=7)

    def link_entry_episode_trade(self, episode_id, trade_id):
        self.linked.append((episode_id, trade_id))

    def save_market_catalog(self, *_args, **_kwargs):
        return None

    def update_trade(self, trade_id, **values):
        self.updated.append((trade_id, values))

    def get_exact_buy_fill_evidence(self, _order_id):
        return ExactFillEvidence(
            "confirmed",
            "buy-1",
            order_status="MATCHED",
            side="BUY",
            requested_size=5.4,
            latest_size_matched=5.4,
            needs_reconciliation=False,
            reconciled_full_fill=True,
            confirmed_size=5.4,
            confirmed_vwap=0.925,
            confirmed_fee_usdc=0.01,
            fee_complete=True,
        )


class _Clob:
    simulation_mode = False

    def __init__(self, vwap=0.925, midpoint=0.50):
        self.vwap = vwap
        self.midpoint = midpoint
        self.orders = []

    def get_buy_book_walk(self, token_id, *, notional_usdc):
        return BuyBookWalk(token_id, 0.91, 0.92, 0.01, self.vwap, 5 / self.vwap, 5, 0.93, 2)

    def place_limit_order(self, **order):
        self.orders.append(order)
        return {"success": True, "orderID": "buy-1"}

    def get_midpoint(self, _token_id):
        return self.midpoint


def _candidate(outcome="No"):
    return {
        "condition_id": "condition-1",
        "market_slug": "market",
        "question": "Will the away team win?",
        "event_id": "event-1",
        "event_slug": "event",
        "outcome": outcome,
        "token_id": "no-token" if outcome == "No" else "yes-token",
        "entry_snapshot_id": 11,
        "entry_episode_id": 3,
        "yes_probability": 0.075,
        "end_date": NOW + timedelta(hours=3),
        "liquidity": 20_000,
        "volume_24h": 1_000,
        "market_tags": "Sports",
    }


def test_buy_revalidates_exact_five_and_submits_fok(monkeypatch) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob()
    config = TradingConfig()
    config.entry = type(config.entry)(0.92, 0.93, 0, 0, 6)
    trader = Trader(repo, clob, config, simulation_mode=False)

    assert trader.execute_buy(_candidate()) == 7
    assert clob.orders == [
        {
            "token_id": "no-token",
            "price": 0.93,
            "size": pytest.approx(5 / 0.925),
            "side": "BUY",
            "order_type": "FOK",
        }
    ]
    created = repo.created[0]
    assert created["outcome"] == "No"
    assert created["buy_amount"] == 5
    assert created["buy_price"] == 0.925
    assert created["status"] is TradeStatus.PENDING_BUY
    assert created["yes_price_at_buy"] == 0.075
    assert repo.linked == [(3, 7)]


def test_existing_manual_wallet_positions_are_never_adopted_or_sold(monkeypatch) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(midpoint=0.10)
    config = TradingConfig()
    trade = SimpleNamespace(id=9, token_id="own-db-token", outcome="Yes")
    trader = Trader(repo, clob, config, simulation_mode=False)

    assert trader.execute_sell(trade) is False
    assert clob.orders == []
    assert repo.updated == []


def test_no_outcome_resolution_uses_selected_payout_without_synthetic_sell() -> None:
    repo, clob = _Repo(), _Clob()
    gamma = SimpleNamespace(
        get_market_by_condition_id=lambda _condition: {
            "conditionId": "condition-1",
            "closed": True,
            "outcomes": ["Yes", "No"],
            "outcomePrices": [0, 1],
            "clobTokenIds": ["yes-token", "no-token"],
            "negRisk": False,
        }
    )
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        token_id="no-token",
        outcome="No",
        buy_order_id="buy-1",
        buy_shares=5.4,
        buy_price=0.925,
    )
    trader = Trader(repo, clob, TradingConfig(), gamma_client=gamma, simulation_mode=False)

    assert trader._handle_midpoint_unavailable(trade, "closed") is False
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.RESOLVED
    assert update["resolution_value"] == 1.0
    assert update["yes_price_at_exit"] == 0.0
    assert update["realized_pnl"] is None
    assert update["settlement_pnl_assumption"] == pytest.approx((1 - 0.925) * 5.4 - 0.01)
    assert clob.orders == []
