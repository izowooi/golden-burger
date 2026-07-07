"""Trader 유령 포지션(매수 미체결) 감지·마감 검증.

GTC limit 매수는 접수 즉시 HOLDING으로 기록되지만(체결 가정), 실제로 체결되지
않은 유령 포지션은 매도 시 CLOB이 "not enough balance ... balance: 0"으로
거절한다. 이때 UNFILLED로 마감해 매도 재시도 루프를 끊는지 검증한다.
"""
from datetime import datetime, timedelta

import pytest

from polybot.config import TradingConfig
from polybot.db.models import TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.trader import Trader, is_zero_balance_error

# 실거래에서 관측된 CLOB 거절 메시지 형태 (golden-fox 계정 실사례)
ZERO_BALANCE_ERROR = (
    "PolyApiException[status_code=400, error_message={'error': "
    "'not enough balance / allowance: the balance is not enough "
    "-> balance: 0, order amount: 47610000'}]"
)
NONZERO_BALANCE_ERROR = (
    "not enough balance / allowance: the balance is not enough "
    "-> balance: 20000000, order amount: 47610000"
)


@pytest.fixture
def repo():
    """in-memory SQLite 기반 repository."""
    Session = init_database(":memory:")
    session = Session()
    yield TradeRepository(session)
    session.close()


class PhantomClob:
    """매도 주문이 실패로 거절되는 CLOB 대역 (매수 GTC 미체결 상황)."""

    def __init__(self, midpoint=0.96, sell_error=ZERO_BALANCE_ERROR):
        self.midpoint = midpoint
        self.sell_error = sell_error
        self.cancelled = []

    def get_midpoint(self, token_id):
        return self.midpoint

    def place_limit_order(self, token_id, price, size, side):
        return {"success": False, "error": self.sell_error}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"success": True}


def make_holding_trade(repo, buy_order_id="0xORDER_HASH"):
    """익절 목표(0.85*1.12=0.952)를 넘긴 midpoint 0.96에서 매도가 트리거되는 HOLDING trade."""
    return repo.create_trade(
        condition_id="0xphantom",
        market_slug="phantom-market",
        question="Will the phantom position be detected?",
        outcome="Yes",
        token_id="tok-phantom",
        buy_price=0.85,
        buy_shares=10.0,
        buy_order_id=buy_order_id,
        max_price=0.85,
        market_end_date=datetime.utcnow() + timedelta(hours=100),
        status=TradeStatus.HOLDING,
    )


class TestIsZeroBalanceError:
    def test_detects_zero_balance(self):
        assert is_zero_balance_error({"error": ZERO_BALANCE_ERROR}) is True

    def test_ignores_nonzero_balance(self):
        assert is_zero_balance_error({"error": NONZERO_BALANCE_ERROR}) is False

    def test_ignores_missing_error(self):
        assert is_zero_balance_error({}) is False


class TestUnfilledPhantomDetection:
    def test_zero_balance_sell_marks_unfilled_and_cancels_buy(self, repo):
        clob = PhantomClob()
        trader = Trader(repo, clob, TradingConfig())
        trade = make_holding_trade(repo)

        sold = trader.execute_sell(trade)

        assert sold is False
        assert clob.cancelled == ["0xORDER_HASH"]
        loaded = repo.get_by_id(trade.id)
        assert loaded.status == TradeStatus.UNFILLED
        assert loaded.exit_reason == "buy_unfilled"

    def test_sim_buy_order_id_is_not_cancelled(self, repo):
        clob = PhantomClob()
        trader = Trader(repo, clob, TradingConfig())
        trade = make_holding_trade(repo, buy_order_id="SIM_BUY_tok-phan")

        trader.execute_sell(trade)

        assert clob.cancelled == []
        assert repo.get_by_id(trade.id).status == TradeStatus.UNFILLED

    def test_nonzero_balance_failure_keeps_holding(self, repo):
        # 부분 체결/allowance 문제(balance > 0)는 유령이 아니다 - HOLDING 유지, 재시도
        clob = PhantomClob(sell_error=NONZERO_BALANCE_ERROR)
        trader = Trader(repo, clob, TradingConfig())
        trade = make_holding_trade(repo)

        sold = trader.execute_sell(trade)

        assert sold is False
        assert clob.cancelled == []
        loaded = repo.get_by_id(trade.id)
        assert loaded.status == TradeStatus.HOLDING
        assert loaded.exit_reason is None
