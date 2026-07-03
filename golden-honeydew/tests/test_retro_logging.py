"""DB 회고 로깅 컬럼 검증 (부록 스펙 §D).

A/B 포스트모템 계약: strategy_name / mode / volume_24h_at_buy / deviation_at_exit
가 실제 SQLite DB에 기록되고, 구 스키마 DB도 init_database ALTER로 호환되는지 확인.
"""
import csv
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from polybot.config import TradingConfig
from polybot.db.models import TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.scanner import MarketScanner
from polybot.strategy.signals import SnapshotPoint
from polybot.strategy.trader import STRATEGY_NAME, Trader

RETRO_COLUMNS = {"strategy_name", "mode", "volume_24h_at_buy", "deviation_at_exit"}


def make_repo(tmp_path):
    Session = init_database(str(tmp_path / "trades.db"))
    return TradeRepository(Session())


def make_clob(midpoint):
    clob = MagicMock()
    clob.get_midpoint.return_value = midpoint
    clob.place_limit_order.return_value = {"success": True, "orderID": "OID"}
    return clob


def make_candidate(**overrides):
    candidate = {
        "condition_id": "0xcond-retro",
        "market_slug": "will-x-happen",
        "question": "Will X happen?",
        "outcome": "Yes",
        "probability": 0.60,
        "token_id": "yes-token",
        "liquidity": 20000.0,
        "volume_24h": 12345.0,
        "entry_reason": "night_dislocation_dev-0.100",
        "deviation": -0.10,
        "median": 0.70,
        "end_date": None,
        "hours_until_resolution": 100.0,
        "market_tags": "",
    }
    candidate.update(overrides)
    return candidate


def make_holding_trade(repo, outcome="Yes", condition_id="0xcond-retro"):
    return repo.create_trade(
        condition_id=condition_id,
        token_id="tok",
        question="Will X happen?",
        outcome=outcome,
        buy_price=0.50,
        buy_shares=10.0,
        buy_timestamp=datetime.utcnow() - timedelta(hours=1),
        market_end_date=None,
        max_price=0.50,
        status=TradeStatus.HOLDING,
    )


class TestCreateTradeRetroColumns:
    def test_execute_buy_records_strategy_mode_and_volume(self, tmp_path):
        repo = make_repo(tmp_path)
        trader = Trader(repo, make_clob(0.60), TradingConfig(), simulation_mode=True)

        trade_id = trader.execute_buy(make_candidate())

        assert trade_id is not None
        trade = repo.get_by_id(trade_id)
        assert trade.strategy_name == STRATEGY_NAME == "honeydew"
        assert trade.mode == "sim"
        assert trade.volume_24h_at_buy == 12345.0

    def test_live_mode_recorded_when_not_simulation(self, tmp_path):
        repo = make_repo(tmp_path)
        trader = Trader(repo, make_clob(0.60), TradingConfig(), simulation_mode=False)

        trade_id = trader.execute_buy(make_candidate())

        assert repo.get_by_id(trade_id).mode == "live"


class TestDeviationAtExit:
    def test_yes_position_records_deviation_vs_median(self, tmp_path):
        repo = make_repo(tmp_path)
        for _ in range(3):
            repo.save_snapshot("0xcond-retro", probability=0.55, volume_24h=100.0)
        trade = make_holding_trade(repo, outcome="Yes")
        trader = Trader(repo, make_clob(0.47), TradingConfig())  # -6% → stop_loss

        assert trader.execute_sell(trade) is True

        updated = repo.get_by_id(trade.id)
        assert updated.exit_reason == "stop_loss"
        # YES 청산가 0.47 - median 0.55 = -0.08
        assert abs(updated.deviation_at_exit - (-0.08)) < 1e-9

    def test_no_position_deviation_uses_yes_equivalent_price(self, tmp_path):
        repo = make_repo(tmp_path)
        for _ in range(3):
            repo.save_snapshot("0xcond-retro", probability=0.55, volume_24h=100.0)
        trade = make_holding_trade(repo, outcome="No")
        trader = Trader(repo, make_clob(0.47), TradingConfig())

        assert trader.execute_sell(trade) is True

        updated = repo.get_by_id(trade.id)
        # NO 0.47 → YES 환산 0.53, 0.53 - median 0.55 = -0.02
        assert abs(updated.deviation_at_exit - (-0.02)) < 1e-9

    def test_deviation_null_when_no_snapshots(self, tmp_path):
        repo = make_repo(tmp_path)
        trade = make_holding_trade(repo, condition_id="0xcond-no-snaps")
        trader = Trader(repo, make_clob(0.47), TradingConfig())

        assert trader.execute_sell(trade) is True
        assert repo.get_by_id(trade.id).deviation_at_exit is None


class TestScannerCandidateVolume:
    def test_candidate_carries_volume_24h(self):
        now = datetime(2026, 1, 7, 8, 0)  # 수요일 08 UTC = quiet hours

        class FakeRepo:
            def get_snapshots_since(self, condition_id, since):
                return [
                    SnapshotPoint(
                        timestamp=now - timedelta(hours=h),
                        probability=0.70,
                        volume_24h=100.0,
                    )
                    for h in (20, 16, 12, 8, 4)
                ]

        scanner = MarketScanner(
            gamma_client=MagicMock(), config=TradingConfig(), repo=FakeRepo()
        )
        market = {
            "conditionId": "0xcond-scan",
            "slug": "will-x-happen",
            "question": "Will X happen?",
            "outcomePrices": ["0.60", "0.40"],
            "clobTokenIds": ["yes-token", "no-token"],
            "outcomes": ["Yes", "No"],
            "liquidity": "20000",
            "volume24hr": "12345.0",
            "endDate": "2030-01-01T00:00:00Z",
        }

        candidates = scanner.scan_buy_candidates([market], now=now)

        assert len(candidates) == 1
        assert candidates[0]["volume_24h"] == 12345.0
        assert candidates[0]["token_id"] == "yes-token"  # dev -0.10 → YES 복원 매수


class TestSchemaMigrationAndCsv:
    def test_init_database_alters_old_schema_db(self, tmp_path):
        """구 스키마 DB 파일 → init_database가 신규 4컬럼을 best-effort ALTER."""
        db_path = tmp_path / "old_schema.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE trades ("
            "id INTEGER PRIMARY KEY, condition_id TEXT, token_id TEXT, "
            "buy_price REAL, status TEXT)"
        )
        conn.execute(
            "INSERT INTO trades (condition_id, token_id, buy_price, status) "
            "VALUES ('0xold', 'tok', 0.5, 'HOLDING')"
        )
        conn.commit()
        conn.close()

        init_database(str(db_path))

        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        old_row = conn.execute(
            "SELECT strategy_name, mode, volume_24h_at_buy, deviation_at_exit "
            "FROM trades WHERE condition_id = '0xold'"
        ).fetchone()
        conn.close()

        assert RETRO_COLUMNS <= cols
        assert old_row == (None, None, None, None)  # 기존 행은 NULL 백필

    def test_init_database_idempotent_on_migrated_db(self, tmp_path):
        db_path = tmp_path / "trades.db"
        init_database(str(db_path))
        init_database(str(db_path))  # ALTER 중복 실행이 예외를 내지 않아야 함

        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        conn.close()
        assert RETRO_COLUMNS <= cols

    def test_csv_export_includes_retro_columns(self, tmp_path):
        repo = make_repo(tmp_path)
        trade = repo.create_trade(
            condition_id="0xcsv",
            token_id="tok",
            question="Will X happen?",
            outcome="Yes",
            buy_price=0.50,
            sell_price=0.47,
            buy_shares=10.0,
            realized_pnl=-0.3,
            buy_timestamp=datetime.utcnow() - timedelta(hours=2),
            sell_timestamp=datetime.utcnow(),
            status=TradeStatus.COMPLETED,
            exit_reason="stop_loss",
            strategy_name="honeydew",
            mode="sim",
            volume_24h_at_buy=12345.0,
            deviation_at_exit=-0.08,
        )

        repo.append_trade_to_csv(trade, tmp_path)

        csv_files = list(tmp_path.glob("trades_*.csv"))
        assert len(csv_files) == 1
        with open(csv_files[0], newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert RETRO_COLUMNS <= set(rows[0].keys())
        assert rows[0]["strategy_name"] == "honeydew"
        assert rows[0]["mode"] == "sim"
        assert rows[0]["volume_24h_at_buy"] == "12345.0"
        assert rows[0]["deviation_at_exit"] == "-0.08"
