"""회고 로깅 컬럼(strategy_name/mode/volume_24h_at_buy/drift_at_exit) 기록 검증.

A/B 포스트모템 계약: SQLite DB만으로 손익 원인 분석이 가능해야 한다.
실제 sqlite 파일 + init_database 경로로 ALTER 마이그레이션까지 검증한다.
"""
import csv
import sqlite3
from datetime import datetime, timedelta

import pytest

from polybot.config import TradingConfig
from polybot.db.models import MarketSnapshot, TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.trader import STRATEGY_NAME, Trader


class FakeClob:
    def __init__(self, midpoint):
        self._midpoint = midpoint
        self.orders = []

    def get_midpoint(self, token_id):
        return self._midpoint

    def place_limit_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"success": True, "orderID": "TEST_ORDER"}


def make_repo(tmp_path):
    Session = init_database(str(tmp_path / "trades_test.db"))
    session = Session()
    return TradeRepository(session), session


def make_candidate(**overrides):
    candidate = {
        "condition_id": "0xcond",
        "market_slug": "will-x-happen",
        "question": "Will X happen by D?",
        "outcome": "Yes",
        "token_index": 0,
        "token_id": "YES_TOKEN",
        "token_price": 0.55,
        "drift": 0.05,
        "consistency": 0.8,
        "vol_accel": 1.5,
        "liquidity": 50000.0,
        "volume_24h": 12345.0,
        "entry_reason": "cascade_up",
        "end_date": datetime.utcnow() + timedelta(hours=100),
        "hours_until_resolution": 100.0,
        "market_tags": "Politics",
    }
    candidate.update(overrides)
    return candidate


def make_holding_trade(repo, **overrides):
    fields = {
        "condition_id": "0xcond",
        "token_id": "YES_TOKEN",
        "token_index": 0,
        "outcome": "Yes",
        "question": "Will X happen by D?",
        "buy_price": 0.60,
        "buy_shares": 10.0,
        "max_price": 0.60,
        "market_end_date": datetime.utcnow() + timedelta(hours=100),
        "status": TradeStatus.HOLDING,
        "strategy_name": STRATEGY_NAME,
        "mode": "sim",
        "volume_24h_at_buy": 12345.0,
    }
    fields.update(overrides)
    return repo.create_trade(**fields)


def add_snapshot(session, condition_id, probability, hours_ago):
    snapshot = MarketSnapshot(
        condition_id=condition_id,
        probability=probability,
        timestamp=datetime.utcnow() - timedelta(hours=hours_ago),
    )
    session.add(snapshot)
    session.commit()


class TestBuyRecordsRetroColumns:
    def test_execute_buy_records_strategy_mode_volume(self, tmp_path):
        repo, session = make_repo(tmp_path)
        trader = Trader(repo, FakeClob(midpoint=0.55), TradingConfig(), mode="sim")

        trade_id = trader.execute_buy(make_candidate())

        assert trade_id is not None
        trade = repo.get_by_id(trade_id)
        assert trade.strategy_name == "grape"
        assert trade.mode == "sim"
        assert trade.volume_24h_at_buy == pytest.approx(12345.0)
        session.close()

    def test_default_mode_is_live(self, tmp_path):
        repo, session = make_repo(tmp_path)
        trader = Trader(repo, FakeClob(midpoint=0.55), TradingConfig())

        trade_id = trader.execute_buy(make_candidate())

        assert repo.get_by_id(trade_id).mode == "live"
        session.close()


class TestSellRecordsDriftAtExit:
    def test_drift_death_exit_records_drift_value(self, tmp_path):
        repo, session = make_repo(tmp_path)
        trade = make_holding_trade(repo)
        # 최근 6h YES 0.62 -> 0.58 (변화 -0.04 <= 0 => drift_death)
        add_snapshot(session, "0xcond", 0.62, hours_ago=5)
        add_snapshot(session, "0xcond", 0.58, hours_ago=1)
        # midpoint 0.61: SL(-8%)도 TP(0.69)도 아님 -> drift_death 경로
        trader = Trader(repo, FakeClob(midpoint=0.61), TradingConfig(), mode="sim")

        sold = trader.execute_sell(trade)

        assert sold is True
        updated = repo.get_by_id(trade.id)
        assert updated.exit_reason == "drift_death"
        assert updated.drift_at_exit == pytest.approx(-0.04)
        session.close()

    def test_stop_loss_exit_also_records_drift_value(self, tmp_path):
        repo, session = make_repo(tmp_path)
        trade = make_holding_trade(repo)
        add_snapshot(session, "0xcond", 0.62, hours_ago=5)
        add_snapshot(session, "0xcond", 0.58, hours_ago=1)
        # midpoint 0.50: -16.7% -> stop_loss (drift_death보다 우선)
        trader = Trader(repo, FakeClob(midpoint=0.50), TradingConfig(), mode="sim")

        sold = trader.execute_sell(trade)

        assert sold is True
        updated = repo.get_by_id(trade.id)
        assert updated.exit_reason == "stop_loss"
        assert updated.drift_at_exit == pytest.approx(-0.04)
        session.close()

    def test_drift_at_exit_null_when_snapshots_insufficient(self, tmp_path):
        repo, session = make_repo(tmp_path)
        trade = make_holding_trade(repo)
        # 스냅샷 없음 -> drift 계산 불가 -> NULL
        trader = Trader(repo, FakeClob(midpoint=0.50), TradingConfig(), mode="sim")

        sold = trader.execute_sell(trade)

        assert sold is True
        updated = repo.get_by_id(trade.id)
        assert updated.exit_reason == "stop_loss"
        assert updated.drift_at_exit is None
        session.close()


class TestOldSchemaMigration:
    OLD_SCHEMA = """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_id VARCHAR NOT NULL,
            market_slug VARCHAR,
            question VARCHAR,
            outcome VARCHAR,
            token_index INTEGER,
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
            max_price FLOAT,
            market_end_date DATETIME,
            hours_until_resolution_at_buy FLOAT,
            drift_at_buy FLOAT,
            consistency_at_buy FLOAT,
            vol_accel_at_buy FLOAT,
            liquidity_at_buy FLOAT,
            market_tags VARCHAR,
            created_at DATETIME,
            updated_at DATETIME
        )
    """

    def test_init_database_alters_old_schema(self, tmp_path):
        db_path = tmp_path / "old_schema.db"
        conn = sqlite3.connect(db_path)
        conn.execute(self.OLD_SCHEMA)
        conn.execute(
            "INSERT INTO trades (condition_id, token_id, status)"
            " VALUES ('0xold', 'TOK', 'COMPLETED')"
        )
        conn.commit()
        conn.close()

        Session = init_database(str(db_path))

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        conn.close()
        assert {"strategy_name", "mode", "volume_24h_at_buy", "drift_at_exit"} <= columns

        # 마이그레이션된 DB에서 ORM read/write가 실제로 동작하는지 확인
        session = Session()
        repo = TradeRepository(session)
        old_trade = repo.get_by_id(1)
        assert old_trade.strategy_name is None  # 기존 행은 NULL 백필
        trade = make_holding_trade(repo, condition_id="0xnew")
        assert repo.get_by_id(trade.id).mode == "sim"
        session.close()

    def test_init_database_idempotent_on_new_schema(self, tmp_path):
        db_path = tmp_path / "fresh.db"
        init_database(str(db_path))
        init_database(str(db_path))  # 재실행해도 ALTER 실패를 무시하고 정상 동작

        conn = sqlite3.connect(db_path)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(trades)")]
        conn.close()
        assert columns.count("strategy_name") == 1


class TestCsvExportIncludesRetroColumns:
    def test_csv_header_and_row_contain_new_columns(self, tmp_path):
        repo, session = make_repo(tmp_path)
        trade = make_holding_trade(
            repo,
            status=TradeStatus.COMPLETED,
            sell_price=0.55,
            sell_timestamp=datetime.utcnow(),
            exit_reason="stop_loss",
            drift_at_exit=-0.04,
        )

        repo.append_trade_to_csv(trade, tmp_path)

        csv_files = list(tmp_path.glob("trades_*.csv"))
        assert len(csv_files) == 1
        with open(csv_files[0], newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        row = rows[0]
        assert row["strategy_name"] == "grape"
        assert row["mode"] == "sim"
        assert float(row["volume_24h_at_buy"]) == pytest.approx(12345.0)
        assert float(row["drift_at_exit"]) == pytest.approx(-0.04)
        session.close()
