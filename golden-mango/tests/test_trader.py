"""Trader 방어 로직 테스트 (midpoint 0 투매 방지 + EXPIRED 마감 처리)
+ 회고 로깅 표준(§A) 기록 검증."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from polybot.config import TradingConfig
from polybot.db.models import TradeStatus
from polybot.strategy.trader import Trader


class FakeClob:
    def __init__(self, midpoint, simulation_mode=False):
        self._midpoint = midpoint
        self.simulation_mode = simulation_mode
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
        self.created = []
        self.skipped = []

    def update_trade(self, trade_id, **kwargs):
        self.updates.append((trade_id, kwargs))

    def create_trade(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=len(self.created), **kwargs)

    def can_reenter(self, condition_id, cooldown_hours):
        return True, "ok"

    def get_position_count(self):
        return 0

    def mark_as_skipped(self, condition_id, reason):
        self.skipped.append((condition_id, reason))


def make_trade(**overrides):
    trade = SimpleNamespace(
        id=1,
        condition_id="0xcond",
        token_id="FAV_TOKEN",
        outcome="Yes",
        question="Will X be resolved YES by D?",
        buy_price=0.90,
        buy_shares=6.0,
        max_price=0.90,
        market_end_date=datetime.utcnow() + timedelta(hours=100),
    )
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


def make_trader(midpoint, simulation_mode=False):
    repo = FakeRepo()
    clob = FakeClob(midpoint, simulation_mode=simulation_mode)
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
        # 가드가 정상 손절을 막지 않는지 확인 (0.90 -> 0.80 = -11.1% < -6%)
        trader, repo, clob = make_trader(midpoint=0.80)
        sold = trader.execute_sell(make_trade())
        assert sold is True
        assert len(clob.orders) == 1
        assert clob.orders[0]["side"] == "SELL"
        assert clob.orders[0]["price"] == 0.80


class TestRetroLoggingColumns:
    """회고 로깅 표준(부록 §A) 컬럼 기록 검증."""

    def make_candidate(self):
        return {
            "condition_id": "0xcond",
            "token_id": "FAV_TOKEN",
            "market_slug": "will-x-happen",
            "question": "Will X be resolved YES by D?",
            "outcome": "Yes",
            "probability": 0.95,
            "liquidity": 50000.0,
            "volume_24h": 12345.0,
            "entry_reason": "carry_y11.65_48.0h_mom+0.005",
            "end_date": datetime.utcnow() + timedelta(hours=48),
            "hours_until_resolution": 48.0,
            "carry_yield": 11.65,
            "momentum_change": 0.005,
            "market_tags": "Politics",
        }

    def test_buy_records_strategy_signal_columns(self):
        trader, repo, clob = make_trader(midpoint=0.94, simulation_mode=False)
        trade_id = trader.execute_buy(self.make_candidate())

        assert trade_id is not None
        assert len(repo.created) == 1
        kwargs = repo.created[0]
        assert kwargs["strategy_name"] == "mango"
        assert kwargs["mode"] == "live"
        assert kwargs["volume_24h_at_buy"] == 12345.0
        # carry_yield_at_buy는 매수 직전 midpoint(0.94) 기준으로 재계산된다
        assert kwargs["carry_yield_at_buy"] is not None
        assert kwargs["carry_yield_at_buy"] > 2.0
        assert kwargs["momentum_6h_at_buy"] == 0.005

    def test_buy_records_sim_mode(self):
        trader, repo, clob = make_trader(midpoint=0.94, simulation_mode=True)
        trader.execute_buy(self.make_candidate())
        assert repo.created[0]["mode"] == "sim"

    def test_sell_records_carry_yield_at_exit(self):
        # 손절 매도 시 청산 시점 캐리(y)가 midpoint 기준으로 기록된다
        trader, repo, clob = make_trader(midpoint=0.80)
        sold = trader.execute_sell(make_trade())
        assert sold is True
        _, kwargs = repo.updates[-1]
        assert kwargs["exit_reason"] == "stop_loss"
        assert kwargs["carry_yield_at_exit"] is not None
        # y = (0.2/0.8) * (8760/~100h) = ~21.9
        assert 20.0 < kwargs["carry_yield_at_exit"] < 24.0

    def test_price_jump_above_band_marks_rapid_jump_skip(self):
        # 스캔~주문 사이 0.985 상한 돌파 -> 매수 없이 쿨다운 skip 기록
        trader, repo, clob = make_trader(midpoint=0.995)
        trade_id = trader.execute_buy(self.make_candidate())
        assert trade_id is None
        assert repo.created == []
        assert repo.skipped == [("0xcond", "rapid_jump")]
