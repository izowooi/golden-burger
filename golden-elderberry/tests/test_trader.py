"""Trader 유령 포지션(매수 미체결) 감지 테스트.

GTC limit 매수는 접수 즉시 HOLDING으로 기록된다(체결 가정). 실제로 체결되지 않은
유령 포지션은 매도 시 CLOB이 "not enough balance ... balance: 0"으로 거절한다.
이때 UNFILLED로 마감해 매도 재시도 루프를 끊고 잔여 매수 주문을 취소하는지 검증한다.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from polybot.config import TradingConfig
from polybot.db.models import TradeStatus
from polybot.strategy.trader import Trader, is_zero_balance_error


class FakeRepo:
    def __init__(self):
        self.updates = []

    def update_trade(self, trade_id, **kwargs):
        self.updates.append((trade_id, kwargs))


class PhantomClob:
    """매도 주문이 '잔고 0'으로 거절되는 CLOB (매수 GTC 미체결 상황)."""

    def __init__(self, midpoint):
        self._midpoint = midpoint
        self.orders = []
        self.cancelled = []

    def get_midpoint(self, token_id):
        return self._midpoint

    def place_limit_order(self, **kwargs):
        self.orders.append(kwargs)
        return {
            "success": False,
            "error": (
                "PolyApiException[status_code=400, error_message={'error': "
                "'not enough balance / allowance: the balance is not enough "
                "-> balance: 0, order amount: 47610000'}]"
            ),
        }

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"success": True}


def make_trade(**overrides):
    trade = SimpleNamespace(
        id=1,
        condition_id="0xcond",
        token_id="YES_TOKEN",
        outcome="Yes",
        question="Will X happen by end of year?",
        buy_price=0.50,
        buy_shares=10.0,
        buy_order_id="0xORDER_HASH",
        buy_timestamp=datetime.utcnow() - timedelta(hours=10),
        max_price=0.50,
        market_end_date=datetime.utcnow() + timedelta(hours=900),
    )
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


class TestUnfilledPhantomDetection:
    def _phantom_trader(self):
        repo = FakeRepo()
        clob = PhantomClob(midpoint=0.60)  # 0.50 -> 0.60 = +20% -> take_profit 발동
        trader = Trader(repo, clob, TradingConfig())
        return trader, repo, clob

    def test_zero_balance_sell_marks_unfilled_and_cancels_buy(self):
        trader, repo, clob = self._phantom_trader()
        trade = make_trade(buy_order_id="0xORDER_HASH")

        sold = trader.execute_sell(trade)

        assert sold is False
        assert clob.cancelled == ["0xORDER_HASH"]
        statuses = [kw.get("status") for _, kw in repo.updates if "status" in kw]
        assert statuses == [TradeStatus.UNFILLED]
        reasons = [kw.get("exit_reason") for _, kw in repo.updates if "exit_reason" in kw]
        assert reasons == ["buy_unfilled"]

    def test_sim_buy_order_id_is_not_cancelled(self):
        trader, repo, clob = self._phantom_trader()
        trade = make_trade(buy_order_id="SIM_BUY_abc123")

        trader.execute_sell(trade)

        assert clob.cancelled == []
        statuses = [kw.get("status") for _, kw in repo.updates if "status" in kw]
        assert statuses == [TradeStatus.UNFILLED]

    def test_nonzero_balance_failure_keeps_holding(self):
        # 부분 체결/allowance 문제(balance > 0)는 유령이 아니다 - 재시도 유지
        trader, repo, clob = self._phantom_trader()
        clob.place_limit_order = lambda **kw: {
            "success": False,
            "error": "not enough balance / allowance: ... -> balance: 20000000, order amount: 47610000",
        }
        trade = make_trade(buy_order_id="0xORDER_HASH")

        sold = trader.execute_sell(trade)

        assert sold is False
        assert clob.cancelled == []
        assert all("status" not in kw for _, kw in repo.updates)


class TestIsZeroBalanceError:
    def test_matches_zero_balance(self):
        assert is_zero_balance_error(
            {"error": "not enough balance / allowance: ... -> balance: 0, order amount: 1"}
        )

    def test_does_not_match_nonzero_balance(self):
        assert not is_zero_balance_error({"error": "balance: 500"})
        assert not is_zero_balance_error(
            {"error": "not enough balance / allowance: ... -> balance: 20000000"}
        )

    def test_no_error_key(self):
        assert not is_zero_balance_error({})
