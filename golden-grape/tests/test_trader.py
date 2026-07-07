"""Trader 방어 로직 테스트 (midpoint 0 투매 방지 + EXPIRED 마감 처리)."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from polybot.config import TradingConfig
from polybot.db.models import TradeStatus
from polybot.strategy.trader import Trader


class FakeClob:
    def __init__(self, midpoint):
        self._midpoint = midpoint
        self.orders = []

    def get_midpoint(self, token_id):
        if isinstance(self._midpoint, Exception):
            raise self._midpoint
        return self._midpoint

    def place_limit_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"success": True, "orderID": "TEST_ORDER"}


class FakeRepo:
    def __init__(self):
        self.updates = []

    def update_trade(self, trade_id, **kwargs):
        self.updates.append((trade_id, kwargs))

    def get_snapshots_since(self, condition_id, since):
        return []


def make_trade(**overrides):
    trade = SimpleNamespace(
        id=1,
        condition_id="0xcond",
        token_id="YES_TOKEN",
        token_index=0,
        outcome="Yes",
        question="Will X happen by D?",
        buy_price=0.60,
        buy_shares=10.0,
        max_price=0.60,
        market_end_date=datetime.utcnow() + timedelta(hours=100),
    )
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


def make_trader(midpoint):
    repo = FakeRepo()
    clob = FakeClob(midpoint)
    trader = Trader(repo, clob, TradingConfig())
    return trader, repo, clob


class TestZeroMidpointGuard:
    def test_zero_midpoint_does_not_dump_position(self):
        # midpoint 0.0은 조회 실패로 취급 - 가짜 stop_loss 투매(0.01 SELL) 금지
        trader, repo, clob = make_trader(midpoint=0.0)
        sold = trader.execute_sell(make_trade())
        assert sold is False
        assert clob.orders == []
        assert repo.updates == []  # EXPIRED 마감도 안 됨 (아직 해결 전)

    def test_zero_midpoint_expires_resolved_market(self):
        # midpoint 0 + 해결 24h 경과 -> EXPIRED 마감 (수동 redeem 필요)
        trader, repo, clob = make_trader(midpoint=0.0)
        trade = make_trade(market_end_date=datetime.utcnow() - timedelta(hours=48))
        sold = trader.execute_sell(trade)
        assert sold is False
        assert clob.orders == []
        assert len(repo.updates) == 1
        _, kwargs = repo.updates[0]
        assert kwargs["status"] == TradeStatus.EXPIRED
        assert kwargs["exit_reason"] == "resolved_unredeemed"

    def test_midpoint_exception_expires_resolved_market(self):
        trader, repo, clob = make_trader(midpoint=RuntimeError("No orderbook"))
        trade = make_trade(market_end_date=datetime.utcnow() - timedelta(hours=48))
        sold = trader.execute_sell(trade)
        assert sold is False
        assert len(repo.updates) == 1
        assert repo.updates[0][1]["status"] == TradeStatus.EXPIRED

    def test_valid_stop_loss_still_sells(self):
        # 가드가 정상 손절을 막지 않는지 확인 (0.60 -> 0.50 = -16.7%)
        trader, repo, clob = make_trader(midpoint=0.50)
        sold = trader.execute_sell(make_trade())
        assert sold is True
        assert len(clob.orders) == 1
        assert clob.orders[0]["side"] == "SELL"
        assert clob.orders[0]["price"] == 0.50


# --- 유령 포지션(매수 미체결) 감지 ---

class PhantomClob(FakeClob):
    """매도 주문이 '잔고 0'으로 거절되는 CLOB (매수 GTC 미체결 상황)."""

    def __init__(self, midpoint):
        super().__init__(midpoint)
        self.cancelled = []

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


class TestUnfilledPhantomDetection:
    def _phantom_trader(self):
        repo = FakeRepo()
        clob = PhantomClob(midpoint=0.50)  # 0.60 -> 0.50 = -16.7% -> stop_loss 발동
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
