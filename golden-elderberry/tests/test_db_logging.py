"""DB 회고 로깅 컬럼 검증 (A/B 포스트모템 계약).

신규 컬럼 strategy_name / mode / volume_24h_at_buy / stabilization_range_at_buy 가
1. 실제 매수 경로(Trader.execute_buy)에서 기록되고
2. 구 스키마 DB 파일에 best-effort ALTER로 추가되고
3. 월별 CSV export에 포함되는지 확인한다.
"""
import csv
import sqlite3
from datetime import datetime, timedelta

import pytest

from polybot.config import TradingConfig
from polybot.db.models import Trade, TradeStatus, STRATEGY_NAME, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.signals import PanicFadeParams, PricePoint, evaluate_panic_fade
from polybot.strategy.trader import Trader

NOW = datetime(2026, 7, 1, 12, 0, 0)


class FakeClob:
    """체결 성공을 흉내내는 CLOB client (주문 없이 DB 기록 경로만 검증)."""

    def __init__(self, midpoint=0.60):
        self.midpoint = midpoint

    def get_midpoint(self, token_id):
        return self.midpoint

    def place_limit_order(self, token_id, price, size, side):
        return {"success": True, "orderID": "fake-order-1"}


def make_candidate(**overrides):
    """scanner.scan_buy_candidates가 만드는 candidate dict 모사."""
    candidate = {
        "condition_id": "0xcond1",
        "market_slug": "test-market",
        "question": "Will the retrospective columns be recorded?",
        "outcome": "Yes",
        "probability": 0.60,
        "token_id": "token-yes",
        "liquidity": 25000.0,
        "volume_24h": 43210.5,
        "entry_reason": "panic_fade_ref0.85_drop0.25",
        "end_date": NOW + timedelta(hours=100),
        "hours_until_resolution": 100.0,
        "market_tags": "Politics",
        "ref_price": 0.85,
        "drop": 0.25,
        "stab_range": 0.015,
    }
    candidate.update(overrides)
    return candidate


class TestRetrospectiveColumns:
    def test_execute_buy_records_new_columns(self, tmp_path):
        """execute_buy가 strategy_name/mode/volume/stab_range를 DB에 남긴다."""
        Session = init_database(str(tmp_path / "trades.db"))
        session = Session()
        repo = TradeRepository(session)
        trader = Trader(repo, FakeClob(), TradingConfig(), simulation_mode=True)

        trade_id = trader.execute_buy(make_candidate())
        assert trade_id is not None

        trade = repo.get_by_id(trade_id)
        assert trade.strategy_name == STRATEGY_NAME == "elderberry"
        assert trade.mode == "sim"
        assert trade.volume_24h_at_buy == pytest.approx(43210.5)
        assert trade.stabilization_range_at_buy == pytest.approx(0.015)
        session.close()

    def test_live_mode_recorded(self, tmp_path):
        """simulation_mode=False면 mode="live"."""
        Session = init_database(str(tmp_path / "trades.db"))
        session = Session()
        repo = TradeRepository(session)
        trader = Trader(repo, FakeClob(), TradingConfig(), simulation_mode=False)

        trade_id = trader.execute_buy(make_candidate())
        assert repo.get_by_id(trade_id).mode == "live"
        session.close()


class TestSignalStabRange:
    def test_entry_signal_exposes_stab_range(self):
        """진입 시그널이 안정화 구간 고저폭(max-min)을 노출한다."""
        raw = []
        h = 47.0
        while h >= 4.0:
            raw.append((h, 0.85))
            h -= 2.0
        raw += [(2.5, 0.80), (2.0, 0.73), (1.5, 0.63),
                (0.6, 0.61), (0.3, 0.60), (0.1, 0.60)]
        series = [PricePoint(NOW - timedelta(hours=h), p) for h, p in raw]

        signal = evaluate_panic_fade(series, 0.60, PanicFadeParams(), NOW)
        assert signal.entry is True
        # 안정화 윈도우(최근 45분) = [0.61, 0.60, 0.60] -> 고저폭 0.01
        assert signal.stab_range == pytest.approx(0.01)


# init_database 도입 전(= 신규 4컬럼이 없는) trades 테이블 DDL
OLD_TRADES_DDL = """
CREATE TABLE trades (
    id INTEGER NOT NULL,
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
    status VARCHAR(12),
    entry_reason VARCHAR,
    exit_reason VARCHAR,
    ref_price_at_buy FLOAT,
    drop_at_buy FLOAT,
    max_price FLOAT,
    market_end_date DATETIME,
    hours_until_resolution_at_buy FLOAT,
    liquidity_at_buy FLOAT,
    market_tags VARCHAR,
    created_at DATETIME,
    updated_at DATETIME,
    PRIMARY KEY (id)
)
"""


class TestAlterMigration:
    def test_old_schema_db_gains_new_columns(self, tmp_path):
        """구 스키마 DB 파일에 init_database가 신규 컬럼을 ALTER로 추가한다."""
        db_path = tmp_path / "old_schema.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(OLD_TRADES_DDL)
        conn.close()

        Session = init_database(str(db_path))

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        conn.close()
        for col in (
            "strategy_name", "mode",
            "volume_24h_at_buy", "stabilization_range_at_buy",
        ):
            assert col in columns

        # 마이그레이션된 DB에 신규 컬럼 포함 insert/조회가 실제로 동작
        session = Session()
        repo = TradeRepository(session)
        trade = repo.create_trade(
            condition_id="0xmigrated",
            token_id="token-yes",
            status=TradeStatus.HOLDING,
            strategy_name=STRATEGY_NAME,
            mode="live",
            volume_24h_at_buy=111.0,
            stabilization_range_at_buy=0.02,
        )
        loaded = repo.get_by_id(trade.id)
        assert loaded.strategy_name == "elderberry"
        assert loaded.mode == "live"
        assert loaded.volume_24h_at_buy == pytest.approx(111.0)
        assert loaded.stabilization_range_at_buy == pytest.approx(0.02)
        session.close()

    def test_init_database_idempotent(self, tmp_path):
        """신규 스키마 DB에 다시 실행해도(ALTER 중복) 조용히 성공한다."""
        db_path = tmp_path / "trades.db"
        init_database(str(db_path))
        init_database(str(db_path))  # 두 번째 호출이 예외 없이 통과해야 함


class TestCsvExport:
    def test_csv_includes_new_columns(self, tmp_path):
        """월별 CSV export 헤더/행에 신규 컬럼이 포함된다."""
        Session = init_database(str(tmp_path / "trades.db"))
        session = Session()
        repo = TradeRepository(session)
        trade = repo.create_trade(
            condition_id="0xcsv",
            token_id="token-yes",
            question="csv?",
            outcome="Yes",
            buy_price=0.60,
            sell_price=0.66,
            realized_pnl=0.6,
            buy_timestamp=NOW,
            sell_timestamp=NOW + timedelta(hours=5),
            status=TradeStatus.COMPLETED,
            exit_reason="take_profit",
            strategy_name=STRATEGY_NAME,
            mode="sim",
            volume_24h_at_buy=43210.5,
            stabilization_range_at_buy=0.015,
        )

        repo.append_trade_to_csv(trade, tmp_path)

        csv_path = tmp_path / f"trades_{trade.sell_timestamp.strftime('%Y-%m')}.csv"
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        row = rows[0]
        assert row["strategy_name"] == "elderberry"
        assert row["mode"] == "sim"
        assert float(row["volume_24h_at_buy"]) == pytest.approx(43210.5)
        assert float(row["stabilization_range_at_buy"]) == pytest.approx(0.015)
        session.close()
