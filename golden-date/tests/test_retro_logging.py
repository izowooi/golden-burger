"""DB 회고 로깅(부록 스펙 §D) 검증 — 신규 컬럼 기록·ALTER 마이그레이션·CSV 헤더."""
import csv
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from polybot.config import TradingConfig
from polybot.db.models import Trade, TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.signals import Snap, evaluate_entry
from polybot.strategy.trader import Trader, STRATEGY_NAME

NOW = datetime(2026, 7, 3, 12, 0, 0)
RUNGS = [
    (24.0, 0.80, 0.95),
    (72.0, 0.75, 0.92),
    (168.0, 0.70, 0.88),
]
ENTRY_HOURS_MIN = 6
LOOKBACK = 6
MIN_CHANGE = -0.01


def make_flat_window(price, points=7, span_hours=6.0, now=NOW):
    """균등 간격 합성 스냅샷 (모멘텀 게이트 통과용 무변화 윈도우)."""
    span = timedelta(hours=span_hours)
    return [Snap(now - span + span * (i / (points - 1)), price) for i in range(points)]


# 구 스키마(회고 컬럼 5개 추가 이전)의 trades 테이블 DDL
OLD_TRADES_DDL = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id VARCHAR NOT NULL,
    market_slug VARCHAR,
    question VARCHAR,
    outcome VARCHAR,
    token_id VARCHAR NOT NULL,
    buy_price FLOAT,
    buy_amount FLOAT,
    buy_shares FLOAT,
    buy_order_id VARCHAR,
    buy_timestamp DATETIME,
    buy_probability FLOAT,
    sell_price FLOAT,
    sell_shares FLOAT,
    sell_order_id VARCHAR,
    sell_timestamp DATETIME,
    sell_probability FLOAT,
    realized_pnl FLOAT,
    status VARCHAR,
    entry_reason VARCHAR,
    exit_reason VARCHAR,
    max_price FLOAT,
    market_end_date DATETIME,
    hours_until_resolution_at_buy FLOAT,
    liquidity_at_buy FLOAT,
    market_tags VARCHAR,
    created_at DATETIME,
    updated_at DATETIME
)
"""

RETRO_COLUMNS = [
    "strategy_name", "mode", "volume_24h_at_buy",
    "ladder_band_at_buy", "momentum_at_buy",
]


@pytest.fixture
def repo():
    """in-memory SQLite 기반 repository."""
    Session = init_database(":memory:")
    session = Session()
    yield TradeRepository(session)
    session.close()


class FakeClob:
    """midpoint/주문을 고정 응답하는 CLOB 대역."""

    def __init__(self, midpoint=0.85):
        self.midpoint = midpoint

    def get_midpoint(self, token_id):
        return self.midpoint

    def place_limit_order(self, token_id, price, size, side):
        return {"success": True, "orderID": "fake-order-1"}


def make_candidate(**overrides):
    """scanner.scan_buy_candidates가 만드는 형태의 candidate dict."""
    end_date = datetime.now(timezone.utc) + timedelta(hours=12)
    candidate = {
        "condition_id": "0xtest",
        "market_slug": "test-market",
        "question": "Will the test pass?",
        "outcome": "Yes",
        "probability": 0.85,
        "token_id": "tok-1",
        "liquidity": 20000.0,
        "volume_24h": 12345.0,
        "entry_reason": "ladder1_12.0h_mom+0.005",
        "end_date": end_date,
        "hours_until_resolution": 12.0,
        "momentum_change": 0.005,
        "ladder_band": 1,
        "market_tags": "Politics",
    }
    candidate.update(overrides)
    return candidate


class TestEntryDecisionLadderBand:
    def test_evaluate_entry_exposes_numeric_band(self):
        """진입 성공 시 EntryDecision.ladder_band에 밴드 번호가 실린다."""
        window = make_flat_window(0.85)
        decision = evaluate_entry(
            price=0.85,
            hours_left=12.0,
            snapshots=window,
            favorite_index=0,
            entry_hours_min=ENTRY_HOURS_MIN,
            rungs=RUNGS,
            momentum_lookback_hours=LOOKBACK,
            momentum_min_change=MIN_CHANGE,
            now=NOW,
        )
        assert decision.entry is True
        assert decision.ladder_band == 1

    def test_band3_number(self):
        window = make_flat_window(0.80)
        decision = evaluate_entry(
            price=0.80,
            hours_left=100.0,
            snapshots=window,
            favorite_index=0,
            entry_hours_min=ENTRY_HOURS_MIN,
            rungs=RUNGS,
            momentum_lookback_hours=LOOKBACK,
            momentum_min_change=MIN_CHANGE,
            now=NOW,
        )
        assert decision.entry is True
        assert decision.ladder_band == 3


class TestRetroColumnsRecorded:
    def test_create_trade_persists_retro_columns(self, repo):
        trade = repo.create_trade(
            condition_id="0xabc",
            token_id="tok-1",
            outcome="Yes",
            question="q",
            buy_price=0.85,
            status=TradeStatus.HOLDING,
            strategy_name="date",
            mode="sim",
            volume_24h_at_buy=9999.5,
            ladder_band_at_buy=2,
            momentum_at_buy=-0.003,
        )
        loaded = repo.get_by_id(trade.id)
        assert loaded.strategy_name == "date"
        assert loaded.mode == "sim"
        assert loaded.volume_24h_at_buy == 9999.5
        assert loaded.ladder_band_at_buy == 2
        assert loaded.momentum_at_buy == -0.003

    def test_execute_buy_records_retro_columns(self, repo):
        """execute_buy → create_trade 경로에서 신규 5컬럼이 실제로 기록된다."""
        trader = Trader(repo, FakeClob(midpoint=0.85), TradingConfig(), simulation_mode=True)
        trade_id = trader.execute_buy(make_candidate())
        assert trade_id is not None

        trade = repo.get_by_id(trade_id)
        assert trade.strategy_name == STRATEGY_NAME == "date"
        assert trade.mode == "sim"
        assert trade.volume_24h_at_buy == 12345.0
        assert trade.ladder_band_at_buy == 1
        assert trade.momentum_at_buy == 0.005

    def test_execute_buy_live_mode(self, repo):
        trader = Trader(repo, FakeClob(midpoint=0.85), TradingConfig(), simulation_mode=False)
        trade_id = trader.execute_buy(make_candidate(condition_id="0xlive"))
        assert trade_id is not None
        assert repo.get_by_id(trade_id).mode == "live"


class TestAlterMigration:
    def test_old_schema_db_gains_retro_columns(self, tmp_path):
        """구 스키마 DB 파일에 init_database가 신규 컬럼을 best-effort ALTER로 추가한다."""
        db_file = tmp_path / "old_trades.db"
        conn = sqlite3.connect(db_file)
        conn.execute(OLD_TRADES_DDL)
        conn.execute(
            "INSERT INTO trades (condition_id, token_id, outcome, status) "
            "VALUES ('0xold', 'tok-old', 'Yes', 'HOLDING')"
        )
        conn.commit()
        conn.close()

        Session = init_database(str(db_file))

        conn = sqlite3.connect(db_file)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        conn.close()
        for col in RETRO_COLUMNS:
            assert col in columns, f"missing column after migration: {col}"

        # 기존 행은 NULL로 보존, 신규 행은 값 기록 가능
        session = Session()
        try:
            repo = TradeRepository(session)
            old_trade = session.query(Trade).filter(Trade.condition_id == "0xold").one()
            assert old_trade.strategy_name is None
            assert old_trade.ladder_band_at_buy is None

            new_trade = repo.create_trade(
                condition_id="0xnew",
                token_id="tok-new",
                outcome="No",
                status=TradeStatus.HOLDING,
                strategy_name="date",
                mode="live",
                volume_24h_at_buy=1.0,
                ladder_band_at_buy=3,
                momentum_at_buy=0.0,
            )
            assert repo.get_by_id(new_trade.id).ladder_band_at_buy == 3
        finally:
            session.close()

    def test_init_database_idempotent_on_new_schema(self, tmp_path):
        """이미 신규 컬럼이 있는 DB에 재실행해도 에러 없이 통과한다."""
        db_file = tmp_path / "new_trades.db"
        init_database(str(db_file))
        init_database(str(db_file))  # 두 번째 호출: ALTER 실패를 조용히 무시해야 함


class TestCsvExport:
    def test_csv_header_and_row_include_retro_columns(self, repo, tmp_path):
        trade = repo.create_trade(
            condition_id="0xcsv",
            token_id="tok-1",
            outcome="Yes",
            question="q",
            buy_price=0.85,
            sell_price=0.95,
            realized_pnl=0.5,
            buy_timestamp=datetime(2026, 7, 1, 0, 0, 0),
            sell_timestamp=datetime(2026, 7, 3, 0, 0, 0),
            status=TradeStatus.COMPLETED,
            strategy_name="date",
            mode="sim",
            volume_24h_at_buy=777.0,
            ladder_band_at_buy=2,
            momentum_at_buy=0.01,
        )
        repo.append_trade_to_csv(trade, tmp_path)

        csv_path = tmp_path / "trades_2026-07.csv"
        assert csv_path.exists()
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        row = rows[0]
        for col in RETRO_COLUMNS:
            assert col in row, f"missing CSV column: {col}"
        assert row["strategy_name"] == "date"
        assert row["mode"] == "sim"
        assert row["volume_24h_at_buy"] == "777.0"
        assert row["ladder_band_at_buy"] == "2"
        assert row["momentum_at_buy"] == "0.01"
