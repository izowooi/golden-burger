"""Trader 청산 경로 유닛테스트 (mock CLOB/repo).

핵심 회귀 검증: midpoint 0.0(조회 실패의 falsy 반환)을 실제 가격으로 오판해
P&L -100% → 가짜 stop_loss 매도(0.01)가 나가지 않아야 한다.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from polybot.config import TradingConfig
from polybot.db.models import TradeStatus
from polybot.strategy.trader import Trader


def make_trade(**overrides):
    trade = MagicMock()
    trade.id = 1
    trade.condition_id = "0xcond"
    trade.token_id = "tok"
    trade.question = "Will something happen by date?"
    trade.outcome = "Yes"
    trade.buy_price = 0.50
    trade.buy_shares = 10.0
    trade.max_price = 0.50
    trade.buy_timestamp = datetime.utcnow() - timedelta(hours=1)
    trade.market_end_date = datetime.utcnow() + timedelta(hours=100)
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


def make_trader(midpoint):
    repo = MagicMock()
    clob = MagicMock()
    if isinstance(midpoint, Exception):
        clob.get_midpoint.side_effect = midpoint
    else:
        clob.get_midpoint.return_value = midpoint
    clob.place_limit_order.return_value = {"success": True, "orderID": "OID"}
    trader = Trader(repo, clob, TradingConfig())
    return trader, repo, clob


class TestZeroMidpoint:
    def test_zero_midpoint_does_not_trigger_fake_stop_loss(self):
        """midpoint 0.0 → 매도 주문 금지 (P&L -100% 오판 방지)."""
        trader, repo, clob = make_trader(0.0)
        trade = make_trade(market_end_date=None)

        assert trader.execute_sell(trade) is False
        clob.place_limit_order.assert_not_called()

    def test_zero_midpoint_expired_market_marked_expired(self):
        """midpoint 0.0 + endDate 24h 경과 → §3.4 EXPIRED 마감 (매도 아님)."""
        trader, repo, clob = make_trader(0.0)
        trade = make_trade(
            market_end_date=datetime.utcnow() - timedelta(hours=30)
        )

        assert trader.execute_sell(trade) is False
        clob.place_limit_order.assert_not_called()
        repo.update_trade.assert_called_once_with(
            trade.id,
            status=TradeStatus.EXPIRED,
            exit_reason="resolved_unredeemed",
            realized_pnl=None,
        )

    def test_midpoint_exception_also_routes_to_expired(self):
        """midpoint 예외 + endDate 경과 → EXPIRED (기존 §3.4 경로 유지)."""
        trader, repo, clob = make_trader(Exception("No orderbook exists"))
        trade = make_trade(
            market_end_date=datetime.utcnow() - timedelta(hours=30)
        )

        assert trader.execute_sell(trade) is False
        repo.update_trade.assert_called_once_with(
            trade.id,
            status=TradeStatus.EXPIRED,
            exit_reason="resolved_unredeemed",
            realized_pnl=None,
        )


class TestUnfilledPhantomDetection:
    """유령 포지션(매수 GTC 미체결) 감지: balance 0 매도 거절 → UNFILLED 마감."""

    ZERO_BALANCE_ERROR = (
        "not enough balance / allowance: ... -> balance: 0, order amount: 47610000"
    )

    def make_phantom_trader(self, error_msg):
        """midpoint 0.47(-6% → stop_loss)로 매도를 유도하되 주문은 거절되는 trader."""
        trader, repo, clob = make_trader(0.47)
        clob.place_limit_order.return_value = {"success": False, "error": error_msg}
        clob.cancel_order.return_value = {"success": True}
        return trader, repo, clob

    def test_zero_balance_sell_marks_unfilled_and_cancels_buy(self):
        """balance 0 거절 → UNFILLED + buy_unfilled + 매수 주문 취소."""
        trader, repo, clob = self.make_phantom_trader(self.ZERO_BALANCE_ERROR)
        trade = make_trade(buy_order_id="0xORDER_HASH")

        assert trader.execute_sell(trade) is False
        clob.cancel_order.assert_called_once_with("0xORDER_HASH")
        update_kwargs = repo.update_trade.call_args.kwargs
        assert update_kwargs["status"] == TradeStatus.UNFILLED
        assert update_kwargs["exit_reason"] == "buy_unfilled"

    def test_sim_buy_order_id_is_not_cancelled(self):
        """SIM_ 접두 주문은 호가창에 없으므로 취소 호출 없이 UNFILLED만 마킹."""
        trader, repo, clob = self.make_phantom_trader(self.ZERO_BALANCE_ERROR)
        trade = make_trade(buy_order_id="SIM_BUY_abc123")

        assert trader.execute_sell(trade) is False
        clob.cancel_order.assert_not_called()
        update_kwargs = repo.update_trade.call_args.kwargs
        assert update_kwargs["status"] == TradeStatus.UNFILLED

    def test_nonzero_balance_failure_keeps_holding(self):
        """balance > 0 거절(부분 체결/allowance)은 유령이 아님 → HOLDING 유지, 재시도."""
        trader, repo, clob = self.make_phantom_trader(
            "not enough balance / allowance: ... -> balance: 20000000, order amount: 47610000"
        )
        trade = make_trade(buy_order_id="0xORDER_HASH")

        assert trader.execute_sell(trade) is False
        clob.cancel_order.assert_not_called()
        repo.update_trade.assert_not_called()


class TestNormalSellPath:
    def test_real_stop_loss_still_sells(self):
        """정상 midpoint에서 P&L -6% → stop_loss 매도는 그대로 동작."""
        trader, repo, clob = make_trader(0.47)  # 0.50 대비 -6%
        trade = make_trade()

        assert trader.execute_sell(trade) is True
        clob.place_limit_order.assert_called_once()
        _, kwargs = clob.place_limit_order.call_args
        assert kwargs["side"] == "SELL"
        assert kwargs["price"] == 0.47
        update_kwargs = repo.update_trade.call_args.kwargs
        assert update_kwargs["status"] == TradeStatus.COMPLETED
        assert update_kwargs["exit_reason"] == "stop_loss"
