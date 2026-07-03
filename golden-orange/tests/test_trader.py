"""Trader 방어 로직 테스트 (midpoint 0 투매 방지 + EXPIRED 마감 + §A 컬럼 기록)."""
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
    def __init__(self, latest_yes=None):
        self.updates = []
        self.created = []
        self.skipped = []
        self._latest_yes = latest_yes

    def update_trade(self, trade_id, **kwargs):
        self.updates.append((trade_id, kwargs))

    def create_trade(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=len(self.created), **kwargs)

    def get_latest_snapshot(self, condition_id):
        if self._latest_yes is None:
            return None
        return SimpleNamespace(probability=self._latest_yes)

    def has_holding(self, condition_id):
        return False

    def is_in_reentry_cooldown(self, condition_id, cooldown_hours):
        return False

    def get_position_count(self):
        return 0

    def mark_as_skipped(self, condition_id, reason):
        self.skipped.append((condition_id, reason))


def make_trade(**overrides):
    trade = SimpleNamespace(
        id=1,
        condition_id="0xcond",
        token_id="NO_TOKEN",
        outcome="No",
        question="Will a nuclear weapon be used this year?",
        buy_price=0.78,
        buy_shares=6.0,
        max_price=0.78,
        buy_timestamp=datetime.utcnow() - timedelta(hours=10),
        market_end_date=datetime.utcnow() + timedelta(hours=200),
        base_price_at_buy=0.08,
        spike_peak_at_buy=0.22,
    )
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


def make_trader(midpoint, latest_yes=None, mode="live"):
    repo = FakeRepo(latest_yes=latest_yes)
    clob = FakeClob(midpoint)
    trader = Trader(repo, clob, TradingConfig(), mode=mode)
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
        # 가드가 정상 손절을 막지 않는지 확인 (0.78 -> 0.68 = -12.8%)
        trader, repo, clob = make_trader(midpoint=0.68)
        sold = trader.execute_sell(make_trade())
        assert sold is True
        assert len(clob.orders) == 1
        assert clob.orders[0]["side"] == "SELL"
        assert clob.orders[0]["price"] == 0.68


class TestRetraceExit:
    def test_retrace_target_sells_and_records_yes_at_exit(self):
        # 스냅샷 최신 YES 0.12 <= 목표 0.15 (= 0.08 + 0.5*(0.22-0.08)) → retrace 익절
        trader, repo, clob = make_trader(midpoint=0.80, latest_yes=0.12)
        sold = trader.execute_sell(make_trade())
        assert sold is True
        assert clob.orders[0]["side"] == "SELL"
        _, kwargs = repo.updates[-1]
        assert kwargs["exit_reason"] == "retrace_target"
        assert kwargs["yes_price_at_exit"] == 0.12

    def test_no_retrace_without_snapshot(self):
        # 스냅샷 없음 → retrace 판단 보류, 다른 청산 조건도 미충족이면 보유 유지
        trader, repo, clob = make_trader(midpoint=0.80, latest_yes=None)
        sold = trader.execute_sell(make_trade())
        assert sold is False
        assert clob.orders == []


class TestBuyRecordsSignalColumns:
    def make_candidate(self, **overrides):
        candidate = {
            "condition_id": "0xcond",
            "market_slug": "nuclear-2026",
            "question": "Will a nuclear weapon be used this year?",
            "outcome": "No",
            "probability": 0.78,
            "yes_price": 0.22,
            "token_id": "NO_TOKEN",
            "liquidity": 20000.0,
            "volume_24h": 2000.0,
            "entry_reason": "fear_spike_fade_base0.08_yes0.22_180m",
            "end_date": datetime.utcnow() + timedelta(hours=200),
            "hours_until_resolution": 200.0,
            "market_tags": "Geopolitics",
            "base_price": 0.08,
            "spike_peak": 0.22,
            "spike_age_minutes": 180.0,
            "vol_mult": 2.0,
        }
        candidate.update(overrides)
        return candidate

    def test_create_trade_records_db_standard_columns(self):
        # 부록 §A: strategy_name / mode / volume_24h_at_buy / 시그널 *_at_buy 기록
        trader, repo, clob = make_trader(midpoint=0.78, mode="sim")
        trade_id = trader.execute_buy(self.make_candidate())
        assert trade_id is not None
        kwargs = repo.created[0]
        assert kwargs["strategy_name"] == "orange"
        assert kwargs["mode"] == "sim"
        assert kwargs["volume_24h_at_buy"] == 2000.0
        assert kwargs["yes_price_at_buy"] == 0.22
        assert kwargs["base_price_at_buy"] == 0.08
        assert kwargs["spike_peak_at_buy"] == 0.22
        assert kwargs["spike_age_minutes_at_buy"] == 180.0
        assert kwargs["vol_mult_at_buy"] == 2.0
        assert kwargs["status"] == TradeStatus.HOLDING

    def test_buy_skipped_when_no_above_band(self):
        # NO > 0.95 = YES 붕괴 완료 → 쿨다운 skip 기록, 주문 없음
        trader, repo, clob = make_trader(midpoint=0.97)
        trade_id = trader.execute_buy(self.make_candidate())
        assert trade_id is None
        assert clob.orders == []
        assert repo.skipped == [("0xcond", "spike_collapsed")]

    def test_buy_skipped_when_no_below_band(self):
        # NO < 0.70 = YES 재점화 → 이번 사이클만 skip (쿨다운 기록 없음)
        trader, repo, clob = make_trader(midpoint=0.65)
        trade_id = trader.execute_buy(self.make_candidate())
        assert trade_id is None
        assert clob.orders == []
        assert repo.skipped == []
