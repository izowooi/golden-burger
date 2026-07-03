"""Trader 방어 로직 테스트 (midpoint 0 투매 방지 + EXPIRED 마감 처리).

추가로 회고 로깅 표준(§A)의 create_trade/update_trade 기록 계약을 검증한다:
strategy_name / mode / volume_24h_at_buy / rolling_min_at_buy / hold_hours_at_exit.
"""
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
        self.created = []
        self.skipped = []

    def update_trade(self, trade_id, **kwargs):
        self.updates.append((trade_id, kwargs))

    def is_reentry_blocked(self, condition_id, cooldown_hours, now=None):
        return False, "ok"

    def get_position_count(self):
        return 0

    def create_trade(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=1, **kwargs)

    def mark_as_skipped(self, condition_id, reason):
        self.skipped.append((condition_id, reason))


def make_trade(**overrides):
    trade = SimpleNamespace(
        id=1,
        condition_id="0xcond",
        token_id="YES_TOKEN",
        outcome="Yes",
        question="Will X happen by end of year?",
        buy_price=0.20,
        buy_shares=25.0,
        buy_timestamp=datetime.utcnow() - timedelta(hours=10),
        max_price=0.20,
        market_end_date=datetime.utcnow() + timedelta(hours=900),
    )
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


def make_trader(midpoint, mode="live"):
    repo = FakeRepo()
    clob = FakeClob(midpoint)
    trader = Trader(repo, clob, TradingConfig(), mode=mode)
    return trader, repo, clob


# --- 방어 4케이스 (zero-midpoint 가드 / EXPIRED) ---

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
        # 가드가 정상 손절을 막지 않는지 확인 (0.20 -> 0.13 = -35% <= -30%)
        trader, repo, clob = make_trader(midpoint=0.13)
        sold = trader.execute_sell(make_trade())
        assert sold is True
        assert len(clob.orders) == 1
        assert clob.orders[0]["side"] == "SELL"
        assert clob.orders[0]["price"] == 0.13


# --- 회고 로깅 표준 (§A) 기록 계약 ---

class TestRetroLoggingContract:
    def make_candidate(self):
        return {
            "condition_id": "0xcond",
            "token_id": "YES_TOKEN",
            "probability": 0.18,
            "outcome": "Yes",
            "question": "Will X happen by end of year?",
            "market_slug": "will-x-happen",
            "liquidity": 15000.0,
            "volume_24h": 4200.0,
            "entry_reason": "bottom_fisher_min0.200_p0.180",
            "end_date": datetime.utcnow() + timedelta(hours=900),
            "hours_until_resolution": 900.0,
            "market_tags": "Politics",
            "rolling_min": 0.20,
            "lookback_days_covered": 19.5,
        }

    def test_buy_records_strategy_name_mode_and_signal_columns(self):
        trader, repo, clob = make_trader(midpoint=0.18, mode="sim")
        trade_id = trader.execute_buy(self.make_candidate())
        assert trade_id == 1
        assert len(repo.created) == 1
        kwargs = repo.created[0]
        assert kwargs["strategy_name"] == "nectarine"
        assert kwargs["mode"] == "sim"
        assert kwargs["volume_24h_at_buy"] == 4200.0
        assert kwargs["rolling_min_at_buy"] == 0.20
        assert kwargs["lookback_days_at_buy"] == 19.5
        assert kwargs["status"] == TradeStatus.HOLDING

    def test_calendar_exit_records_hold_hours_at_exit(self):
        # 보유 121h > 120h -> max_holding 청산 + hold_hours_at_exit 기록
        trader, repo, clob = make_trader(midpoint=0.22)
        trade = make_trade(
            buy_timestamp=datetime.utcnow() - timedelta(hours=121)
        )
        sold = trader.execute_sell(trade)
        assert sold is True
        _, kwargs = repo.updates[-1]
        assert kwargs["exit_reason"] == "max_holding"
        assert kwargs["status"] == TradeStatus.COMPLETED
        assert abs(kwargs["hold_hours_at_exit"] - 121.0) < 0.1

    def test_buy_skips_when_price_left_band_upward(self):
        # 스캔~주문 사이 반등해 밴드 상단(0.50) 초과 -> 쿨다운 skip 기록
        trader, repo, clob = make_trader(midpoint=0.55)
        trade_id = trader.execute_buy(self.make_candidate())
        assert trade_id is None
        assert repo.created == []
        assert repo.skipped == [("0xcond", "price_above_band")]
