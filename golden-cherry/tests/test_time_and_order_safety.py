"""Golden Cherry의 시간 경계와 주문 실패 방어 로직 테스트."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from polybot_observability import (
    ClobResponseUnavailableError,
    SubmissionEvidenceError,
)
from polybot.config import TimeBasedConfig, TradingConfig
from polybot.db.models import TradeStatus
from polybot.strategy.scanner import is_valid_time_entry
from polybot.strategy.trader import Trader


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FakeRepo:
    def __init__(self):
        self.updates = []
        self.created = []

    def is_already_traded(self, condition_id):
        return False

    def get_position_count(self):
        return 0

    def mark_as_skipped(self, condition_id, reason):
        return None

    def create_trade(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=1, **kwargs)

    def update_trade(self, trade_id, **kwargs):
        self.updates.append((trade_id, kwargs))


class FakeClob:
    def __init__(self, midpoint=0.85, results=None):
        self.midpoint = midpoint
        self.results = list(results or [{"success": True, "orderID": "ORDER"}])
        self.orders = []
        self.cancelled = []

    def get_midpoint(self, token_id):
        return self.midpoint

    def place_limit_order(self, **kwargs):
        self.orders.append(kwargs)
        if self.results:
            return self.results.pop(0)
        return {"success": True, "orderID": "ORDER"}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"success": True}


def make_trade(**overrides):
    trade = SimpleNamespace(
        id=1,
        condition_id="0xcondition",
        token_id="TOKEN",
        outcome="Yes",
        question="Will this market resolve?",
        buy_price=0.85,
        buy_shares=6.0,
        buy_order_id="BUY_ORDER",
        max_price=0.85,
        market_end_date=utcnow_naive() + timedelta(hours=100),
    )
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


def make_candidate(condition_id="0xbuy"):
    return {
        "condition_id": condition_id,
        "token_id": "YES_TOKEN",
        "probability": 0.80,
        "outcome": "Yes",
        "question": "Will this market resolve?",
        "market_slug": "will-this-market-resolve",
        "liquidity": 20_000.0,
        "entry_reason": "time_based_24h",
        "end_date": utcnow_naive() + timedelta(hours=24),
        "hours_until_resolution": 24.0,
    }


def test_zero_to_120_hour_window_accepts_only_future_markets():
    now = datetime.now(timezone.utc)

    valid, _, _ = is_valid_time_entry(
        now + timedelta(minutes=1), 120, 0
    )
    too_early, reason, _ = is_valid_time_entry(
        now + timedelta(hours=121), 120, 0
    )
    resolved, resolved_reason, _ = is_valid_time_entry(
        now - timedelta(minutes=1), 120, 0
    )

    assert valid is True
    assert too_early is False and reason.startswith("too_early")
    assert resolved is False and resolved_reason == "already_resolved"


def test_active_time_exit_window_is_not_opened_as_a_new_position():
    valid, reason, _ = is_valid_time_entry(
        datetime.now(timezone.utc) + timedelta(hours=6),
        120,
        0,
        12,
    )

    assert valid is False
    assert reason.startswith("inside_exit_window")


def test_entry_window_change_does_not_sell_an_existing_position():
    config = TradingConfig(
        time_based=TimeBasedConfig(
            enabled=True,
            entry_hours_min=0,
            entry_hours_max=120,
            exit_hours=0,
        )
    )
    repo = FakeRepo()
    clob = FakeClob(midpoint=0.85)
    trader = Trader(repo, clob, config)

    sold = trader.execute_sell(
        make_trade(market_end_date=utcnow_naive() + timedelta(hours=300))
    )

    assert sold is False
    assert clob.orders == []


def test_past_end_date_does_not_trigger_time_exit():
    config = TradingConfig(
        time_based=TimeBasedConfig(
            enabled=True,
            entry_hours_min=0,
            entry_hours_max=120,
            exit_hours=12,
        )
    )
    repo = FakeRepo()
    clob = FakeClob(midpoint=0.85)
    trader = Trader(repo, clob, config)

    sold = trader.execute_sell(
        make_trade(market_end_date=utcnow_naive() - timedelta(hours=1))
    )

    assert sold is False
    assert clob.orders == []


def test_partial_token_balance_retries_once_and_records_actual_size():
    repo = FakeRepo()
    clob = FakeClob(
        midpoint=0.70,
        results=[
            {
                "success": False,
                "error": (
                    "not enough balance / allowance: the balance is not enough "
                    "-> balance: 5500000, order amount: 6000000"
                ),
            },
            {"success": True, "orderID": "PARTIAL_SELL"},
        ],
    )
    trader = Trader(repo, clob, TradingConfig())

    assert trader.execute_sell(make_trade()) is True
    assert [order["size"] for order in clob.orders] == [6.0, 5.445]
    assert repo.updates[-1][1]["sell_shares"] == 5.445
    assert repo.updates[-1][1]["status"] == TradeStatus.COMPLETED


def test_unknown_submission_result_does_not_create_or_complete_trade():
    unknown = {
        "success": False,
        "error": "주문 POST 결과가 불확실하여 동일 token/side를 격리했습니다",
        "submission_outcome_unknown": True,
        "quarantined": True,
    }
    buy_repo = FakeRepo()
    buy_trader = Trader(buy_repo, FakeClob(midpoint=0.80, results=[unknown]), TradingConfig())
    sell_repo = FakeRepo()
    sell_trader = Trader(sell_repo, FakeClob(midpoint=0.70, results=[unknown]), TradingConfig())

    assert buy_trader.execute_buy(make_candidate()) is None
    assert sell_trader.execute_sell(make_trade()) is False
    assert buy_repo.created == []
    assert sell_repo.updates == []


def test_unavailable_zero_balance_buy_order_is_quarantined():
    zero_balance = {
        "success": False,
        "error": (
            "not enough balance / allowance: the balance is not enough "
            "-> balance: 0, order amount: 6000000"
        ),
    }
    repo = FakeRepo()
    clob = FakeClob(midpoint=0.70, results=[zero_balance])

    def unavailable_cancel(order_id):
        clob.cancelled.append(order_id)
        try:
            raise ClobResponseUnavailableError("aged out")
        except ClobResponseUnavailableError as cause:
            raise SubmissionEvidenceError("unavailable") from cause

    clob.cancel_order = unavailable_cancel
    trader = Trader(repo, clob, TradingConfig())

    assert trader.execute_sell(make_trade()) is False
    assert clob.cancelled == ["BUY_ORDER"]
    assert repo.updates[-1][1]["status"] == TradeStatus.QUARANTINED
    assert repo.updates[-1][1]["realized_pnl"] is None
