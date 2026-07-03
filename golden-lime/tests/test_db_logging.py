"""DB 회고 로깅 컬럼 검증 (A/B 포스트모템 계약).

trades의 strategy_name / mode / volume_24h_at_buy / vol_mult_at_buy가
scanner candidate → Trader.execute_buy → create_trade 경로로 실제 기록되는지,
그리고 init_database의 best-effort ALTER가 구 스키마 DB 파일을 마이그레이션
하는지 검증한다.
"""
import csv
import sqlite3
from datetime import datetime, timedelta

from sqlalchemy import text

from polybot.config import TradingConfig
from polybot.db.models import Trade, TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.signals import PricePoint, ShockParams, evaluate_entry
from polybot.strategy.trader import Trader, STRATEGY_NAME

NEW_COLUMNS = ["strategy_name", "mode", "volume_24h_at_buy", "vol_mult_at_buy"]


class FakeClobClient:
    """주문 없이 고정 가격/성공 응답만 돌려주는 CLOB 대역."""

    def __init__(self, midpoint: float = 0.55):
        self.midpoint = midpoint

    def get_midpoint(self, token_id: str) -> float:
        return self.midpoint

    def place_limit_order(self, token_id, price, size, side):
        return {"success": True, "orderID": "fake-order-1"}


def make_candidate(**overrides) -> dict:
    candidate = {
        "condition_id": "0xcondition",
        "market_slug": "test-market",
        "question": "Will the shock follow through?",
        "outcome": "Yes",
        "token_index": 0,
        "probability": 0.55,
        "token_id": "token-yes",
        "liquidity": 50000.0,
        "entry_reason": "jump_up_0.15",
        "jump_size": 0.15,
        "base_price": 0.40,
        "volume_24h": 25000.0,
        "vol_mult": 2.5,
        "end_date": datetime.utcnow() + timedelta(hours=100),
        "hours_until_resolution": 100.0,
        "market_tags": "Politics",
    }
    candidate.update(overrides)
    return candidate


def make_repo() -> TradeRepository:
    Session = init_database(":memory:")
    return TradeRepository(Session())


def test_execute_buy_records_retro_columns():
    """매수 시 strategy_name="lime", mode, volume_24h_at_buy, vol_mult_at_buy 기록."""
    repo = make_repo()
    trader = Trader(repo, FakeClobClient(0.55), TradingConfig(), simulation_mode=False)

    trade_id = trader.execute_buy(make_candidate())

    assert trade_id is not None
    trade = repo.get_by_id(trade_id)
    assert trade.strategy_name == "lime"
    assert trade.strategy_name == STRATEGY_NAME
    assert trade.mode == "live"
    assert trade.volume_24h_at_buy == 25000.0
    assert trade.vol_mult_at_buy == 2.5
    assert trade.status == TradeStatus.HOLDING


def test_execute_buy_records_sim_mode():
    """simulation_mode=True → mode="sim"."""
    repo = make_repo()
    trader = Trader(repo, FakeClobClient(0.55), TradingConfig(), simulation_mode=True)

    trade_id = trader.execute_buy(make_candidate())

    assert repo.get_by_id(trade_id).mode == "sim"


def test_evaluate_entry_exposes_vol_mult():
    """진입 판정 결과에 vol_mult(현재/윈도우 평균 배수)가 실린다."""
    now = datetime(2026, 7, 3, 12, 0, 0)
    base, top = 0.40, 0.55
    series = [
        PricePoint(now - timedelta(minutes=m), p, 10_000.0)
        for m, p in [
            (350, base), (300, base), (250, base), (200, base), (150, base),
            (120, 0.475), (90, top - 0.01),
            (60, top), (45, top), (30, top), (15, top), (5, top),
        ]
    ]
    decision = evaluate_entry(series, top, 25_000.0, ShockParams(), now=now)

    assert decision.enter is True
    assert abs(decision.vol_mult - 2.5) < 1e-9  # 25k / 평균 10k


def test_init_database_migrates_old_schema(tmp_path):
    """구 스키마 DB 파일(신규 4컬럼 없음)이 ALTER로 마이그레이션된다."""
    db_path = tmp_path / "trades_old.db"

    # 신규 스키마 생성 후 4컬럼을 DROP해 '구 스키마' DB 파일을 재현
    init_database(str(db_path))
    conn = sqlite3.connect(db_path)
    for col in NEW_COLUMNS:
        conn.execute(f"ALTER TABLE trades DROP COLUMN {col}")
    conn.commit()
    old_cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    conn.close()
    assert not (set(NEW_COLUMNS) & old_cols)

    # 재초기화 → best-effort ALTER가 4컬럼을 추가해야 한다
    Session = init_database(str(db_path))
    conn = sqlite3.connect(db_path)
    migrated_cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    conn.close()
    assert set(NEW_COLUMNS) <= migrated_cols

    # 마이그레이션된 DB에 ORM으로 기록/조회가 실제로 동작한다
    repo = TradeRepository(Session())
    trade = repo.create_trade(
        condition_id="0xmigrated",
        token_id="token-x",
        strategy_name="lime",
        mode="sim",
        volume_24h_at_buy=12345.0,
        vol_mult_at_buy=3.1,
    )
    loaded = repo.get_by_id(trade.id)
    assert loaded.strategy_name == "lime"
    assert loaded.volume_24h_at_buy == 12345.0


def test_init_database_idempotent_on_new_schema(tmp_path):
    """이미 신규 스키마인 DB에 재초기화해도 에러 없이 동작한다 (best-effort)."""
    db_path = tmp_path / "trades_new.db"
    init_database(str(db_path))
    Session = init_database(str(db_path))  # 2번째 호출 - ALTER 실패를 조용히 흡수

    session = Session()
    session.execute(text("SELECT strategy_name, mode FROM trades")).fetchall()
    session.close()


def test_csv_export_includes_retro_columns(tmp_path):
    """월별 CSV 헤더/행에 신규 컬럼이 동기화된다."""
    repo = make_repo()
    trade = repo.create_trade(
        condition_id="0xcsv",
        token_id="token-y",
        question="csv test",
        outcome="Yes",
        buy_price=0.55,
        sell_price=0.62,
        realized_pnl=0.7,
        buy_timestamp=datetime.utcnow(),
        sell_timestamp=datetime.utcnow(),
        status=TradeStatus.COMPLETED,
        exit_reason="take_profit",
        strategy_name="lime",
        mode="live",
        volume_24h_at_buy=25000.0,
        vol_mult_at_buy=2.5,
    )

    repo.append_trade_to_csv(trade, tmp_path)

    csv_files = list(tmp_path.glob("trades_*.csv"))
    assert len(csv_files) == 1
    with open(csv_files[0], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    row = rows[0]
    assert row["strategy_name"] == "lime"
    assert row["mode"] == "live"
    assert row["volume_24h_at_buy"] == "25000.0"
    assert row["vol_mult_at_buy"] == "2.5"
