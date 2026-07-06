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

def test_cycle_stats_roundtrip(tmp_path):
    """max_positions 튜닝 회고용 사이클 통계가 그대로 적재/조회되는지."""
    from polybot.db.models import CycleStat

    repo = make_repo(tmp_path)
    repo.save_cycle_stats(
        markets_scanned=1429, buy_candidates=128,
        holdings_before=0, holdings_after=100,
        max_positions=100, buy_amount_usdc=10.0,
        bought=100, cap_skips=28, cooldown_skips=0, failed_buys=0,
    )
    row = repo.session.query(CycleStat).one()
    assert row.cap_skips == 28
    assert row.max_positions == 100
    assert row.holdings_after == 100
    assert row.buy_amount_usdc == 10.0


def test_capped_candidate_dedup_within_window(tmp_path):
    """같은 시장의 상한 스킵은 24h 내 1회만 기록 (사이클마다 중복 적재 방지)."""
    from polybot.db.models import CappedCandidate

    repo = make_repo(tmp_path)
    first = repo.save_capped_candidate(
        condition_id="0xcap", question="q", yes_price=0.085,
        rolling_min=0.085, hours_left=4258.8,
    )
    dup = repo.save_capped_candidate(
        condition_id="0xcap", question="q", yes_price=0.084,
    )
    other = repo.save_capped_candidate(
        condition_id="0xother", question="q2", yes_price=0.12,
    )
    assert first is not None and dup is None and other is not None
    assert repo.session.query(CappedCandidate).count() == 2
