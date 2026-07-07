"""Trader 유령 포지션(매수 GTC 미체결) 감지·마감 테스트.

GTC limit 매수는 접수 즉시 HOLDING으로 기록되지만(체결 가정), 실제로 체결되지
않은 포지션은 매도 시 CLOB이 "not enough balance ... balance: 0"으로 거절한다.
이때 매수 주문을 취소하고 UNFILLED로 마감해 매도 재시도 루프를 끊는지 검증한다.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from polybot.config import TradingConfig
from polybot.db.models import TradeStatus
from polybot.strategy.trader import Trader, is_zero_balance_error


class PhantomClob:
    """매도 주문을 zero-balance 사유로 거절하는 CLOB fake."""

    def __init__(self, midpoint: float):
        self._midpoint = midpoint
        self.cancelled = []

    def get_midpoint(self, token_id):
        return self._midpoint

    def place_limit_order(self, **kwargs):
        return {
            "success": False,
            "error": (
                "PolyApiException[status_code=400, "
                "error_message={'error': 'not enough balance / allowance: "
                "... -> balance: 0, order amount: 25000000'}]"
            ),
        }

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"canceled": [order_id]}


class FakeRepo:
    def __init__(self):
        self.updates = []

    def update_trade(self, trade_id, **kwargs):
        self.updates.append((trade_id, kwargs))


def make_trade(**overrides):
    trade = SimpleNamespace(
        id=1,
        condition_id="0xcond",
        token_id="YES_TOKEN",
        token_index=0,
        outcome="Yes",
        question="Will X happen by end of year?",
        buy_price=0.20,
        buy_shares=25.0,
        buy_order_id="0xORDER_HASH",
        max_price=0.20,
        market_end_date=datetime.utcnow() + timedelta(hours=900),
    )
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


def make_phantom_trader():
    repo = FakeRepo()
    # midpoint 0.275 = 매수가 0.20 대비 +37.5% -> take_profit(+12%) 발동 -> SELL 시도
    clob = PhantomClob(midpoint=0.275)
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)
    return trader, repo, clob


class TestUnfilledPhantomDetection:
    def test_zero_balance_sell_marks_unfilled_and_cancels_buy(self):
        trader, repo, clob = make_phantom_trader()
        trade = make_trade(buy_order_id="0xORDER_HASH")

        sold = trader.execute_sell(trade)

        assert sold is False
        assert clob.cancelled == ["0xORDER_HASH"]
        statuses = [kw.get("status") for _, kw in repo.updates if "status" in kw]
        assert statuses == [TradeStatus.UNFILLED]
        reasons = [kw.get("exit_reason") for _, kw in repo.updates if "exit_reason" in kw]
        assert reasons == ["buy_unfilled"]

    def test_sim_buy_order_id_is_not_cancelled(self):
        trader, repo, clob = make_phantom_trader()
        trade = make_trade(buy_order_id="SIM_BUY_abc123")

        trader.execute_sell(trade)

        assert clob.cancelled == []
        statuses = [kw.get("status") for _, kw in repo.updates if "status" in kw]
        assert statuses == [TradeStatus.UNFILLED]

    def test_nonzero_balance_failure_keeps_holding(self):
        # 부분 체결/allowance 문제(balance > 0)는 유령이 아니다 - 재시도 유지
        trader, repo, clob = make_phantom_trader()
        clob.place_limit_order = lambda **kw: {
            "success": False,
            "error": "not enough balance / allowance: ... -> balance: 20000000, order amount: 47610000",
        }
        trade = make_trade(buy_order_id="0xORDER_HASH")

        sold = trader.execute_sell(trade)

        assert sold is False
        assert clob.cancelled == []
        assert all("status" not in kw for _, kw in repo.updates)


class TestZeroBalancePattern:
    def test_matches_zero_balance_rejection(self):
        assert is_zero_balance_error(
            {"error": "not enough balance / allowance: ... -> balance: 0, order amount: 1"}
        )

    def test_ignores_nonzero_balance_rejection(self):
        assert not is_zero_balance_error({"error": "balance: 500"})
        assert not is_zero_balance_error(
            {"error": "not enough balance / allowance: ... -> balance: 20000000"}
        )

    def test_ignores_missing_error(self):
        assert not is_zero_balance_error({})
