"""DB 회고 로깅 표준(§A) 검증: 컬럼 존재, best-effort ALTER, CSV export."""
import csv
import sqlite3
from datetime import datetime

from sqlalchemy import text

from polybot.db.models import init_database, TradeStatus, STRATEGY_NAME
from polybot.db.repository import TradeRepository


def make_repo(tmp_path, name="trades.db"):
    Session = init_database(str(tmp_path / name))
    return TradeRepository(Session())


def test_create_trade_persists_retro_columns(tmp_path):
    """strategy_name/mode/volume_24h_at_buy/시그널 컬럼이 DB에 저장된다."""
    repo = make_repo(tmp_path)
    trade = repo.create_trade(
        strategy_name=STRATEGY_NAME,
        mode="live",
        condition_id="0xcond",
        token_id="YES_TOKEN",
        outcome="Yes",
        question="Will X happen?",
        buy_price=0.18,
        buy_timestamp=datetime.utcnow(),
        volume_24h_at_buy=4200.0,
        rolling_min_at_buy=0.20,
        lookback_days_at_buy=19.5,
        status=TradeStatus.HOLDING,
    )

    loaded = repo.get_by_id(trade.id)
    assert loaded.strategy_name == "nectarine"
    assert loaded.mode == "live"
    assert loaded.volume_24h_at_buy == 4200.0
    assert loaded.rolling_min_at_buy == 0.20
    assert loaded.lookback_days_at_buy == 19.5


def test_init_database_alters_legacy_db(tmp_path):
    """신규 컬럼이 없는 기존 DB도 best-effort ALTER로 열린다 (§A.8)."""
    db_path = tmp_path / "legacy.db"

    # 신규 컬럼이 빠진 legacy trades 테이블을 수동 생성
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE trades ("
        "id INTEGER PRIMARY KEY, condition_id TEXT NOT NULL, "
        "token_id TEXT NOT NULL, question TEXT)"
    )
    conn.commit()
    conn.close()

    # init_database가 ALTER로 컬럼을 추가하고 crash 없이 동작해야 한다
    Session = init_database(str(db_path))
    session = Session()
    try:
        columns = {
            row[1]
            for row in session.execute(text("PRAGMA table_info(trades)"))
        }
        for required in (
            "strategy_name", "mode", "volume_24h_at_buy",
            "rolling_min_at_buy", "lookback_days_at_buy", "hold_hours_at_exit",
        ):
            assert required in columns
    finally:
        session.close()


def test_csv_export_includes_signal_columns(tmp_path):
    """월별 CSV export에 회고 표준 컬럼이 포함된다 (§A.7)."""
    repo = make_repo(tmp_path)
    trade = repo.create_trade(
        strategy_name=STRATEGY_NAME,
        mode="sim",
        condition_id="0xcond",
        token_id="YES_TOKEN",
        outcome="Yes",
        question="Will X happen?",
        buy_price=0.18,
        buy_timestamp=datetime(2026, 7, 1, 0, 0, 0),
        sell_price=0.22,
        sell_timestamp=datetime(2026, 7, 6, 1, 0, 0),
        realized_pnl=1.0,
        exit_reason="max_holding",
        volume_24h_at_buy=4200.0,
        rolling_min_at_buy=0.20,
        lookback_days_at_buy=19.5,
        hold_hours_at_exit=121.0,
        status=TradeStatus.COMPLETED,
    )

    repo.append_trade_to_csv(trade, tmp_path)

    csv_path = tmp_path / "trades_2026-07.csv"
    assert csv_path.exists()
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    row = rows[0]
    assert row["strategy_name"] == "nectarine"
    assert row["mode"] == "sim"
    assert row["exit_reason"] == "max_holding"
    assert float(row["rolling_min_at_buy"]) == 0.20
    assert float(row["lookback_days_at_buy"]) == 19.5
    assert float(row["hold_hours_at_exit"]) == 121.0
    assert float(row["volume_24h_at_buy"]) == 4200.0
