"""Live lifecycle transitions must be driven by exact CLOB evidence."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from polybot.config import TradingConfig
from polybot.db.models import TradeStatus
from polybot.db.repository import ExactFillEvidence
from polybot.strategy.trader import Trader


def full_fill(
    order_id,
    *,
    side,
    size=5.2,
    price=0.90,
    fee=0.0,
):
    return ExactFillEvidence(
        "confirmed",
        order_id,
        order_status="MATCHED",
        side=side,
        requested_size=size,
        latest_size_matched=size,
        needs_reconciliation=False,
        reconciled_full_fill=True,
        confirmed_size=size,
        confirmed_vwap=price,
        confirmed_fee_usdc=fee,
        fee_complete=True,
        matched_at="2026-08-14T00:00:00Z",
    )


class FakeRepo:
    def __init__(self, buy_evidence=None, sell_evidence=None):
        self.buy_evidence = buy_evidence
        self.sell_evidence = sell_evidence
        self.updates = []
        self.created = []

    def get_exact_buy_fill_evidence(self, order_id):
        return self.buy_evidence

    def get_exact_sell_fill_evidence(self, order_id):
        return self.sell_evidence

    def update_trade(self, trade_id, **kwargs):
        self.updates.append((trade_id, kwargs))

    def is_already_traded(self, condition_id):
        return False

    def get_position_count(self):
        return 0

    def get_open_notional_usdc(self):
        return 0.0

    def mark_as_skipped(self, condition_id, reason):
        return None

    def create_trade(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=1, **kwargs)


class FakeClob:
    def __init__(self, *, simulation_mode=False, midpoint=0.80):
        self.simulation_mode = simulation_mode
        self.midpoint = midpoint
        self.cancelled = []
        self.orders = []

    def get_midpoint(self, token_id):
        return self.midpoint

    def get_conditional_token_balance(self, token_id):
        return 5.2

    def place_limit_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"success": True, "orderID": "accepted-order"}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {
            "success": True,
            "verified_order_status": "CANCELED",
            "verified_size_matched": 0.0,
        }


def candidate():
    return {
        "condition_id": "condition",
        "token_id": "token",
        "probability": 0.80,
        "outcome": "Yes",
        "question": "Will it resolve?",
        "market_slug": "will-it-resolve",
        "liquidity": 100_000.0,
        "entry_reason": "time_based",
        "end_date": datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(hours=24),
    }


def trade(**overrides):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    values = {
        "id": 1,
        "condition_id": "condition",
        "token_id": "token",
        "outcome": "Yes",
        "question": "Will it resolve?",
        "buy_price": 0.90,
        "buy_shares": 5.2,
        "buy_order_id": "buy-order",
        "buy_timestamp": now - timedelta(hours=1),
        "max_price": 0.90,
        "market_end_date": now + timedelta(hours=24),
        "sell_order_id": "sell-order",
        "sell_shares": None,
        "exit_reason": "stop_loss_pending_confirmed_fill",
        "pending_sell_remaining_shares": 0.0,
        "realized_pnl": None,
        "pnl_basis": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_live_buy_acceptance_creates_pending_buy():
    repo = FakeRepo()
    trader = Trader(repo, FakeClob(), TradingConfig())
    assert trader.execute_buy(candidate()) == 1
    assert repo.created[-1]["status"] == TradeStatus.PENDING_BUY


def test_simulated_buy_remains_hypothetical_holding():
    repo = FakeRepo()
    trader = Trader(
        repo,
        FakeClob(simulation_mode=True),
        TradingConfig(),
    )
    assert trader.execute_buy(candidate()) == 1
    assert repo.created[-1]["status"] == TradeStatus.HOLDING


def test_exact_full_buy_fill_activates_actual_holding():
    repo = FakeRepo(
        buy_evidence=full_fill(
            "buy-order", side="BUY", size=5.2, price=0.91, fee=0.01
        )
    )
    trader = Trader(repo, FakeClob(), TradingConfig())
    assert trader.reconcile_pending_buy(trade()) is True
    update = repo.updates[-1][1]
    assert update["status"] == TradeStatus.HOLDING
    assert update["buy_shares"] == 5.2
    assert update["buy_price"] == 0.91
    assert update["buy_amount"] == pytest.approx(5.2 * 0.91 + 0.01)


def test_expired_exact_live_zero_fill_is_cancelled_and_unfilled():
    evidence = ExactFillEvidence(
        "pending",
        "buy-order",
        order_status="LIVE",
        side="BUY",
        requested_size=5.2,
        latest_size_matched=0.0,
        needs_reconciliation=True,
    )
    repo = FakeRepo(buy_evidence=evidence)
    clob = FakeClob()
    trader = Trader(
        repo,
        clob,
        TradingConfig(pending_buy_ttl_minutes=30),
    )
    assert trader.reconcile_pending_buy(trade()) is False
    assert clob.cancelled == ["buy-order"]
    assert repo.updates[-1][1]["status"] == TradeStatus.UNFILLED


def test_legacy_live_buy_without_full_fill_is_reclassified():
    evidence = ExactFillEvidence(
        "pending",
        "buy-order",
        order_status="LIVE",
        side="BUY",
        requested_size=5.2,
        latest_size_matched=0.0,
        needs_reconciliation=True,
    )
    repo = FakeRepo(buy_evidence=evidence)
    trader = Trader(repo, FakeClob(), TradingConfig())
    assert trader.reclassify_unconfirmed_live_buy(trade()) is True
    assert repo.updates[-1][1]["status"] == TradeStatus.PENDING_BUY


def test_live_sell_acceptance_never_creates_request_assumption_pnl():
    repo = FakeRepo()
    trader = Trader(repo, FakeClob(midpoint=0.70), TradingConfig())
    assert trader.execute_sell(trade()) is False
    update = repo.updates[-1][1]
    assert update["status"] == TradeStatus.PENDING_SELL
    assert "realized_pnl" not in update


def test_exact_zero_fee_buy_and_sell_complete_with_actual_net_pnl():
    repo = FakeRepo(
        buy_evidence=full_fill("buy-order", side="BUY", price=0.90),
        sell_evidence=full_fill("sell-order", side="SELL", price=0.95),
    )
    trader = Trader(repo, FakeClob(), TradingConfig())
    assert trader.reconcile_pending_sell(trade()) is True
    update = repo.updates[-1][1]
    assert update["status"] == TradeStatus.COMPLETED
    assert update["realized_pnl"] == pytest.approx((0.95 - 0.90) * 5.2)
    assert update["pnl_basis"] == (
        "exact_reconciled_buy_sell_confirmed_fills_net_known_fees"
    )


def test_terminal_zero_fill_sell_returns_to_holding():
    zero = ExactFillEvidence(
        "terminal_zero_fill",
        "sell-order",
        order_status="CANCELED",
        side="SELL",
        requested_size=5.2,
        latest_size_matched=0.0,
        needs_reconciliation=False,
        confirmed_size=0.0,
    )
    repo = FakeRepo(sell_evidence=zero)
    trader = Trader(repo, FakeClob(), TradingConfig())
    assert trader.reconcile_pending_sell(trade()) is False
    update = repo.updates[-1][1]
    assert update["status"] == TradeStatus.HOLDING
    assert update["sell_order_id"] is None


def test_unknown_sell_fee_keeps_pending_sell_fail_closed():
    unknown_fee = ExactFillEvidence(
        "confirmed",
        "sell-order",
        order_status="MATCHED",
        side="SELL",
        requested_size=5.2,
        latest_size_matched=5.2,
        needs_reconciliation=False,
        reconciled_full_fill=True,
        confirmed_size=5.2,
        confirmed_vwap=0.95,
        confirmed_fee_usdc=None,
        fee_complete=False,
    )
    repo = FakeRepo(sell_evidence=unknown_fee)
    trader = Trader(repo, FakeClob(), TradingConfig())
    assert trader.reconcile_pending_sell(trade()) is False
    assert repo.updates == []


def test_explicit_closed_market_blocks_dead_book_sell():
    class ClosedGamma:
        def get_market_by_condition_id(self, condition_id):
            return {"closed": True, "active": False, "acceptingOrders": False}

    repo = FakeRepo()
    clob = FakeClob(midpoint=0.50)
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=ClosedGamma(),
    )
    assert trader.execute_sell(trade()) is False
    assert clob.orders == []
    assert repo.updates == []
