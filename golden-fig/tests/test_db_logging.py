"""DB 회고 로깅 검증 (strategy_name / mode / volume_24h_at_buy / yes_price_at_exit).

A/B 포스트모템 계약: SQLite DB만으로 교차 봇 분석이 가능해야 한다.
실제 init_database + TradeRepository로 컬럼이 기록되는지 검증한다.
"""
import csv
import sqlite3
from datetime import datetime, timedelta

import pytest

from polybot.config import TradingConfig
from polybot.db.models import Trade, TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.trader import Trader, STRATEGY_NAME


class FakeClob:
    def __init__(self, midpoint):
        self._midpoint = midpoint
        self.orders = []

    def get_midpoint(self, token_id):
        return self._midpoint

    def place_limit_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"success": True, "orderID": "TEST_ORDER"}


@pytest.fixture
def repo():
    Session = init_database(":memory:")
    session = Session()
    yield TradeRepository(session)
    session.close()


def make_candidate(**overrides):
    candidate = {
        "condition_id": "0xcond",
        "market_slug": "will-x-happen",
        "question": "Will X happen by D?",
        "outcome": "No",
        "probability": 0.85,
        "yes_price": 0.15,
        "token_id": "NO_TOKEN",
        "liquidity": 50000.0,
        "volume_24h": 12345.0,
        "entry_reason": "hope_crusher_test",
        "end_date": datetime.utcnow() + timedelta(hours=100),
        "hours_until_resolution": 100.0,
        "market_tags": "Politics",
    }
    candidate.update(overrides)
    return candidate


class TestCreateTradeLogging:
    def test_buy_records_strategy_name_mode_and_volume(self, repo):
        trader = Trader(repo, FakeClob(0.85), TradingConfig(), simulation_mode=True)
        trade_id = trader.execute_buy(make_candidate())
        assert trade_id is not None

        trade = repo.get_by_id(trade_id)
        assert trade.strategy_name == STRATEGY_NAME == "fig"
        assert trade.mode == "sim"
        assert trade.volume_24h_at_buy == 12345.0

    def test_live_mode_recorded_by_default(self, repo):
        trader = Trader(repo, FakeClob(0.85), TradingConfig())
        trade_id = trader.execute_buy(make_candidate())
        assert repo.get_by_id(trade_id).mode == "live"


class TestSellLogging:
    def test_stop_loss_sell_records_yes_price_at_exit(self, repo):
        # 0.85 매수 -> 0.70 (-17.6%) = stop_loss 청산
        trade = repo.create_trade(
            condition_id="0xcond",
            token_id="NO_TOKEN",
            outcome="No",
            question="Will X happen by D?",
            buy_price=0.85,
            buy_shares=6.0,
            max_price=0.85,
            status=TradeStatus.HOLDING,
            market_end_date=datetime.utcnow() + timedelta(hours=100),
        )
        trader = Trader(repo, FakeClob(0.70), TradingConfig())
        assert trader.execute_sell(trade) is True

        updated = repo.get_by_id(trade.id)
        assert updated.status == TradeStatus.COMPLETED
        # 청산 시점 YES 가격 = 1 - NO 매도가
        assert updated.yes_price_at_exit == pytest.approx(0.30)


class TestCsvExportSync:
    def test_csv_contains_new_columns(self, repo, tmp_path):
        trade = repo.create_trade(
            condition_id="0xcond",
            token_id="NO_TOKEN",
            outcome="No",
            question="Will X happen by D?",
            buy_price=0.85,
            buy_shares=6.0,
            sell_price=0.90,
            sell_timestamp=datetime(2026, 7, 1, 12, 0, 0),
            realized_pnl=0.30,
            status=TradeStatus.COMPLETED,
            strategy_name="fig",
            mode="sim",
            volume_24h_at_buy=12345.0,
            yes_price_at_exit=0.10,
        )
        repo.append_trade_to_csv(trade, tmp_path)

        csv_path = tmp_path / "trades_2026-07.csv"
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        row = rows[0]
        assert row["strategy_name"] == "fig"
        assert row["mode"] == "sim"
        assert float(row["volume_24h_at_buy"]) == 12345.0
        assert float(row["yes_price_at_exit"]) == 0.10


OLD_SCHEMA_DDL = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
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
    yes_price_at_buy FLOAT,
    liquidity_at_buy FLOAT,
    market_tags VARCHAR,
    created_at DATETIME,
    updated_at DATETIME
)
"""

NEW_COLUMNS = {"volume_24h_at_buy", "yes_price_at_exit", "strategy_name", "mode"}


class TestAlterMigration:
    def test_init_database_adds_new_columns_to_old_schema_db(self, tmp_path):
        db_path = tmp_path / "old_trades.db"
        conn = sqlite3.connect(db_path)
        conn.execute(OLD_SCHEMA_DDL)
        conn.execute(
            "INSERT INTO trades (condition_id, token_id, status) "
            "VALUES ('0xold', 'TOK', 'HOLDING')"
        )
        conn.commit()
        conn.close()

        Session = init_database(str(db_path))

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        conn.close()
        assert NEW_COLUMNS <= columns

        # 마이그레이션 후 기존 행 조회 + 신규 컬럼 기록이 동작해야 한다
        session = Session()
        try:
            old = session.query(Trade).filter_by(condition_id="0xold").one()
            assert old.strategy_name is None
            repo = TradeRepository(session)
            repo.update_trade(old.id, strategy_name="fig", mode="live")
            assert repo.get_by_id(old.id).mode == "live"
        finally:
            session.close()

    def test_init_database_idempotent_on_migrated_db(self, tmp_path):
        db_path = tmp_path / "trades.db"
        init_database(str(db_path))
        # 이미 컬럼이 있는 DB에 다시 실행해도 예외 없이 통과해야 한다
        init_database(str(db_path))
