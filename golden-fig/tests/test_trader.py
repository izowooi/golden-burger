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


def make_trade(**overrides):
    trade = SimpleNamespace(
        id=1,
        condition_id="0xcond",
        token_id="NO_TOKEN",
        outcome="No",
        question="Will X happen by D?",
        buy_price=0.85,
        buy_shares=6.0,
        max_price=0.85,
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
        # midpoint 0.0은 조회 실패로 취급 - stop_loss 투매(0.01 SELL) 금지
        trader, repo, clob = make_trader(midpoint=0.0)
        sold = trader.execute_sell(make_trade())
        assert sold is False
        assert clob.orders == []
        assert repo.updates == []  # EXPIRED 마감도 안 됨 (아직 해결 전)

    def test_zero_midpoint_expires_resolved_market(self):
        # midpoint 0 + 해결 24h 경과 -> EXPIRED 마감 (수동 redeem 필요)
        trader, repo, clob = make_trader(midpoint=0.0)
        trade = make_trade(
            market_end_date=datetime.utcnow() - timedelta(hours=48)
        )
        sold = trader.execute_sell(trade)
        assert sold is False
        assert clob.orders == []
        assert len(repo.updates) == 1
        _, kwargs = repo.updates[0]
        assert kwargs["status"] == TradeStatus.EXPIRED
        assert kwargs["exit_reason"] == "resolved_unredeemed"
        assert kwargs["realized_pnl"] is None

    def test_midpoint_exception_expires_resolved_market(self):
        trader, repo, clob = make_trader(midpoint=RuntimeError("No orderbook"))
        trade = make_trade(
            market_end_date=datetime.utcnow() - timedelta(hours=48)
        )
        sold = trader.execute_sell(trade)
        assert sold is False
        assert len(repo.updates) == 1
        assert repo.updates[0][1]["status"] == TradeStatus.EXPIRED

    def test_valid_stop_loss_still_sells(self):
        # 가드가 정상 손절을 막지 않는지 확인 (0.85 -> 0.70 = -17.6%)
        trader, repo, clob = make_trader(midpoint=0.70)
        sold = trader.execute_sell(make_trade())
        assert sold is True
        assert len(clob.orders) == 1
        assert clob.orders[0]["side"] == "SELL"
        assert clob.orders[0]["price"] == 0.70
