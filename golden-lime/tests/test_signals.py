"""Shock Follow 진입/청산 시그널 테스트 (합성 스냅샷 fixture 기반).

진입 O/X 경계 케이스를 순수 함수 evaluate_entry로 검증한다.
스냅샷은 YES 가격 기준이며, NO 방향은 evaluate_entry 내부에서 1-p 변환된다.
"""
from datetime import datetime, timedelta

from polybot.strategy.signals import (
    PricePoint,
    ShockParams,
    capped_take_profit_target,
    detect_jump,
    evaluate_entry,
    invert_series,
    is_momentum_dead,
    merge_price_points,
)

NOW = datetime(2026, 7, 3, 12, 0, 0)
PARAMS = ShockParams()  # 기본값: jump 6h/+0.10, base [0.15, 0.70], current<=0.85 등
BASE_VOLUME = 10_000.0
CONFIRMED_VOLUME = 25_000.0  # 평균(10k)의 2.5배 >= 2.0배


def make_series(spec, volume=BASE_VOLUME):
    """합성 YES 가격 시계열 생성.

    Args:
        spec: (minutes_ago, yes_price) 튜플 리스트
        volume: 각 포인트의 volume_24h
    """
    return [
        PricePoint(NOW - timedelta(minutes=m), p, volume)
        for m, p in spec
    ]


def flat_then_jump(base=0.40, top=0.55):
    """6h 윈도우: base에서 횡보하다 최근 2h에 top으로 점프 후 고점 유지."""
    return make_series([
        (350, base), (300, base), (250, base), (200, base), (150, base),
        (120, base + (top - base) * 0.5),
        (90, top - 0.01),
        (60, top), (45, top), (30, top), (15, top), (5, top),
    ])


# --- 진입 O 케이스 ---

def test_valid_up_jump_enters_yes():
    """케이스 1: 점프 +0.15, 기준가 0.40, 고점 유지, 거래량 2.5배 → YES 진입."""
    series = flat_then_jump(base=0.40, top=0.55)
    decision = evaluate_entry(series, 0.55, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is True
    assert decision.outcome_index == 0
    assert decision.reason == "jump_up"
    assert abs(decision.jump_size - 0.15) < 1e-9
    assert abs(decision.base_price - 0.40) < 1e-9


def test_jump_exactly_at_threshold_enters():
    """케이스 2 (경계): 점프가 정확히 +0.10이면 진입한다 (>=)."""
    series = flat_then_jump(base=0.40, top=0.50)
    decision = evaluate_entry(series, 0.50, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is True
    assert abs(decision.jump_size - 0.10) < 1e-9


def test_down_jump_buys_no():
    """케이스 3: YES 급락(0.60→0.45) = NO 급등(0.40→0.55) → NO 진입."""
    series = make_series([
        (350, 0.60), (300, 0.60), (250, 0.60), (200, 0.60), (150, 0.60),
        (120, 0.52), (90, 0.46),
        (60, 0.45), (45, 0.45), (30, 0.45), (15, 0.45), (5, 0.45),
    ])
    decision = evaluate_entry(series, 0.45, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is True
    assert decision.outcome_index == 1
    assert decision.reason == "jump_down"
    # NO 토큰 기준: base = 1-0.60 = 0.40, current = 1-0.45 = 0.55
    assert abs(decision.base_price - 0.40) < 1e-6
    assert abs(decision.token_price - 0.55) < 1e-6


# --- 진입 X 케이스 (점프 조건) ---

def test_jump_below_threshold_rejected():
    """케이스 4: 점프 +0.09 < 0.10 → 진입 거부."""
    series = flat_then_jump(base=0.40, top=0.49)
    decision = evaluate_entry(series, 0.49, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is False
    assert decision.reason == "no_jump"


def test_base_price_too_low_rejected():
    """케이스 5: 기준가 0.10 < base_min 0.15 (붕괴권 롱샷) → 진입 거부."""
    series = flat_then_jump(base=0.10, top=0.30)
    decision = evaluate_entry(series, 0.30, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is False
    assert decision.reason == "no_jump"


def test_base_price_too_high_rejected():
    """케이스 6: 기준가 0.72 > base_max 0.70 (이미 favorite) → 진입 거부."""
    series = flat_then_jump(base=0.72, top=0.84)
    decision = evaluate_entry(series, 0.84, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is False
    assert decision.reason == "no_jump"


def test_current_price_above_max_rejected():
    """케이스 7: 현재가 0.86 > current_max 0.85 (러닝룸 없음) → 진입 거부."""
    series = flat_then_jump(base=0.65, top=0.86)
    decision = evaluate_entry(series, 0.86, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is False
    assert decision.reason == "no_jump"


def test_flat_market_rejected():
    """케이스 8: 횡보 시장 (점프 없음) → 진입 거부."""
    series = make_series([(m, 0.50) for m in (350, 300, 250, 200, 150, 90, 60, 30, 5)])
    decision = evaluate_entry(series, 0.50, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is False
    assert decision.reason == "no_jump"


# --- 진입 X 케이스 (고점 유지 / 거래량) ---

def test_pullback_too_deep_rejected():
    """케이스 9: 점프 후 고점 0.60 대비 되돌림 0.05 > 0.02 → 노이즈로 판정."""
    series = make_series([
        (350, 0.40), (300, 0.40), (250, 0.40), (200, 0.40), (150, 0.40),
        (120, 0.50), (90, 0.58),
        (45, 0.60), (30, 0.60), (15, 0.56), (5, 0.55),
    ])
    decision = evaluate_entry(series, 0.55, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is False
    assert decision.reason == "pullback_too_deep"


def test_pullback_exactly_at_max_enters():
    """케이스 10 (경계): 되돌림이 정확히 0.02면 진입한다 (<=)."""
    series = make_series([
        (350, 0.40), (300, 0.40), (250, 0.40), (200, 0.40), (150, 0.40),
        (120, 0.50), (90, 0.55),
        (45, 0.57), (30, 0.57), (15, 0.56), (5, 0.55),
    ])
    decision = evaluate_entry(series, 0.55, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is True


def test_volume_not_confirmed_rejected():
    """케이스 11: 거래량 1.5배 < 2.0배 (노이즈 급변) → 진입 거부."""
    series = flat_then_jump(base=0.40, top=0.55)
    decision = evaluate_entry(series, 0.55, 15_000.0, PARAMS, now=NOW)
    assert decision.enter is False
    assert decision.reason == "volume_unconfirmed"


def test_volume_missing_rejected():
    """케이스 12: 스냅샷에 volume 데이터가 없으면 확인 불가 → 진입 거부."""
    series = flat_then_jump(base=0.40, top=0.55)
    no_volume = [PricePoint(p.timestamp, p.price, None) for p in series]
    decision = evaluate_entry(no_volume, 0.55, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is False
    assert decision.reason == "volume_unconfirmed"


# --- 진입 X 케이스 (윈도우 유효성) ---

def test_too_few_snapshots_rejected():
    """케이스 13: 스냅샷 3개 < min 5개 → window_invalid (cold-start 폴백 금지)."""
    series = make_series([(90, 0.40), (30, 0.50), (5, 0.55)])
    decision = evaluate_entry(series, 0.55, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is False
    assert decision.reason == "window_invalid"


def test_poor_time_coverage_rejected():
    """케이스 14: 포인트 6개지만 전부 최근 50분 (커버리지 < 6h의 50%) → invalid."""
    series = make_series([
        (50, 0.40), (40, 0.44), (30, 0.48), (20, 0.52), (10, 0.55), (5, 0.55),
    ])
    decision = evaluate_entry(series, 0.55, CONFIRMED_VOLUME, PARAMS, now=NOW)
    assert decision.enter is False
    assert decision.reason == "window_invalid"


# --- detect_jump 단독 (윈도우 내 최저가 기준) ---

def test_detect_jump_uses_window_minimum():
    """점프 기준가는 '윈도우 내 최저가' - 시작가가 아니라 최저가 대비로 판정."""
    window = make_series([(300, 0.50), (200, 0.38), (100, 0.45), (30, 0.52)])
    jump = detect_jump(window, 0.52, 0.10, 0.15, 0.70, 0.85)
    assert jump is not None
    assert abs(jump.base_price - 0.38) < 1e-9
    assert abs(jump.jump_size - 0.14) < 1e-9


# --- 청산 시그널 ---

def test_momentum_dead_when_flat():
    """3h 동안 가격 변화 0 → 모멘텀 사망 (change <= 0)."""
    points = make_series([(170, 0.55), (120, 0.56), (60, 0.55), (10, 0.55)])
    assert is_momentum_dead(points, 0.55, 3.0, now=NOW) is True


def test_momentum_alive_when_rising():
    """3h 동안 +0.05 상승 → 모멘텀 유지."""
    points = make_series([(170, 0.50), (120, 0.52), (60, 0.54), (10, 0.55)])
    assert is_momentum_dead(points, 0.55, 3.0, now=NOW) is False


def test_momentum_death_deferred_on_shallow_data():
    """매수 직후처럼 최근 데이터만 있으면 판단 보류 (즉시 청산 방지)."""
    points = make_series([(20, 0.55), (10, 0.55)])
    assert is_momentum_dead(points, 0.55, 3.0, now=NOW) is False


def test_take_profit_target_capped_at_099():
    """고가 진입 건의 익절 목표가는 0.99로 캡 (도달 불가 목표가 방지)."""
    assert capped_take_profit_target(0.90, 0.12) == 0.99
    assert abs(capped_take_profit_target(0.50, 0.12) - 0.56) < 1e-9


# --- 유틸 ---

def test_invert_series():
    points = make_series([(60, 0.40), (30, 0.55)])
    inverted = invert_series(points)
    assert abs(inverted[0].price - 0.60) < 1e-9
    assert abs(inverted[1].price - 0.45) < 1e-9
    assert inverted[0].volume_24h == BASE_VOLUME


def test_merge_price_points_dedupes_by_minute():
    """백필 병합: 같은 분의 포인트는 DB 스냅샷 우선, 나머지는 시간순 병합."""
    db = make_series([(60, 0.40), (30, 0.50)])
    backfill = [
        PricePoint(NOW - timedelta(minutes=60), 0.99),  # 중복 시각 → 무시
        PricePoint(NOW - timedelta(minutes=45), 0.45),  # 신규 → 추가
    ]
    merged = merge_price_points(db, backfill)
    assert len(merged) == 3
    assert [round(p.price, 2) for p in merged] == [0.40, 0.45, 0.50]
