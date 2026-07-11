"""Bottom Fisher 진입/청산 시그널 테스트 (합성 스냅샷 fixture 기반).

진입 O/X 경계 케이스를 순수 함수 evaluate_bottom_fisher로 검증한다.
스냅샷은 YES 가격 기준이며, 이 전략은 YES 매수 고정이라 환산이 없다.
"""
from datetime import datetime, timedelta

from polybot.strategy.signals import (
    EPSILON,
    BottomFisherParams,
    PricePoint,
    capped_take_profit_target,
    evaluate_bottom_fisher,
    evaluate_exit,
    merge_price_series,
)

NOW = datetime(2026, 7, 3, 12, 0, 0)
PARAMS = BottomFisherParams()  # 기본값: 20일 룩백, 최근 24h 제외, 밴드 [0.03, 0.50]


def make_series(spec):
    """합성 YES 가격 시계열 생성.

    Args:
        spec: (hours_ago, yes_price) 튜플 리스트
    """
    return [PricePoint(NOW - timedelta(hours=h), p) for h, p in spec]


def baseline_series(floor=0.20):
    """20일 룩백의 95%(19일)를 일별 1점 이상으로 채운 기본 시계열.

    과거 구간(24h 이전)의 최저가 = floor. 최근 24h에는 floor보다 높은
    포인트만 있어 기준선에 영향을 주지 않는다.
    """
    spec = []
    for hours_ago in range(470, 1, -24):
        price = floor if hours_ago == 206 else floor + 0.05
        spec.append((hours_ago, price))
    return make_series(spec)


# --- 진입 O 케이스 ---

def test_new_low_below_rolling_min_enters():
    """케이스 1: 현재가가 20일 최저가(0.20)보다 낮으면(0.18) 진입."""
    signal = evaluate_bottom_fisher(baseline_series(0.20), 0.18, PARAMS, now=NOW)
    assert signal.entry is True
    assert abs(signal.rolling_min - 0.20) < 1e-12
    assert signal.current_price == 0.18


def test_tie_with_rolling_min_enters():
    """케이스 2 (경계): 현재가 == 롤링 최저가 → 동률 허용(<=) 진입."""
    signal = evaluate_bottom_fisher(baseline_series(0.20), 0.20, PARAMS, now=NOW)
    assert signal.entry is True


def test_float_noise_above_min_still_enters():
    """케이스 3 (EPSILON): 부동소수점 표현 오차만큼 높은 값은 동률로 취급.

    0.1 + 0.2 - 0.1 = 0.20000000000000004 같은 표현 오차가 진입을
    막으면 안 된다 (EPSILON=1e-9 흡수).
    """
    noisy = 0.20 + 1e-12
    signal = evaluate_bottom_fisher(baseline_series(0.20), noisy, PARAMS, now=NOW)
    assert signal.entry is True


def test_band_lower_boundary_enters():
    """케이스 4 (경계): 현재가가 정확히 prob_min(0.03)이면 진입 가능."""
    signal = evaluate_bottom_fisher(baseline_series(0.05), 0.03, PARAMS, now=NOW)
    assert signal.entry is True


def test_band_upper_boundary_enters():
    """케이스 5 (경계): 현재가가 정확히 prob_max(0.50)이고 신저가면 진입."""
    signal = evaluate_bottom_fisher(baseline_series(0.55), 0.50, PARAMS, now=NOW)
    assert signal.entry is True


def test_recent_24h_dip_excluded_from_reference():
    """케이스 6: 최근 24h의 더 낮은 저점은 기준선에서 제외된다.

    최근 12h에 0.15 저점이 있어도 기준 최저가는 과거 구간의 0.20이므로
    현재가 0.19는 신저가로 판정돼 진입한다. (진행 중인 하락 자체를
    기준선에 넣으면 신저가 판정이 자기 비교가 되는 문제 방지.)
    """
    series = baseline_series(0.20) + [PricePoint(NOW - timedelta(hours=12), 0.15)]
    signal = evaluate_bottom_fisher(series, 0.19, PARAMS, now=NOW)
    assert signal.entry is True
    assert abs(signal.rolling_min - 0.20) < 1e-12


# --- 진입 X 케이스 ---

def test_price_above_rolling_min_rejected():
    """케이스 7: 현재가(0.25)가 롤링 최저가(0.20)보다 높으면 거부."""
    signal = evaluate_bottom_fisher(baseline_series(0.20), 0.25, PARAMS, now=NOW)
    assert signal.entry is False
    assert signal.reason.startswith("above_rolling_min")


def test_price_meaningfully_above_min_by_more_than_epsilon_rejected():
    """케이스 8 (경계): EPSILON을 초과해 높으면 거부 (1e-6 > 1e-9)."""
    signal = evaluate_bottom_fisher(
        baseline_series(0.20), 0.20 + 1e-6, PARAMS, now=NOW
    )
    assert signal.entry is False


def test_price_below_band_rejected():
    """케이스 9: 현재가 < prob_min(0.03) → 붕괴 tail, 거부."""
    signal = evaluate_bottom_fisher(baseline_series(0.05), 0.02, PARAMS, now=NOW)
    assert signal.entry is False
    assert signal.reason.startswith("price_out_of_band")


def test_price_above_band_rejected():
    """케이스 10: 현재가 > prob_max(0.50) → 밴드 밖, 거부 (신저가여도)."""
    signal = evaluate_bottom_fisher(baseline_series(0.60), 0.55, PARAMS, now=NOW)
    assert signal.entry is False
    assert signal.reason.startswith("price_out_of_band")


def test_invalid_window_rejected():
    """케이스 11: 커버리지 부족(2일 < 19일) → window_invalid, 진입 금지.

    백필 실패 + 스냅샷 부족 상황 - 관대한 cold-start 폴백 금지.
    """
    series = make_series([(48, 0.30), (36, 0.28), (24, 0.27), (12, 0.26), (2, 0.25)])
    signal = evaluate_bottom_fisher(series, 0.20, PARAMS, now=NOW)
    assert signal.entry is False
    assert signal.reason == "window_invalid"


def test_too_few_points_rejected():
    """케이스 12: 포인트 4개 < 20개 → window_invalid."""
    series = make_series([(470, 0.30), (300, 0.28), (150, 0.27), (2, 0.25)])
    signal = evaluate_bottom_fisher(series, 0.20, PARAMS, now=NOW)
    assert signal.entry is False
    assert signal.reason == "window_invalid"


def test_stale_series_outside_lookback_rejected():
    """케이스 13: 모든 포인트가 20일 룩백 밖 → 빈 윈도우, 진입 금지."""
    series = make_series([(600, 0.30), (560, 0.25), (540, 0.22), (520, 0.21), (500, 0.20)])
    signal = evaluate_bottom_fisher(series, 0.15, PARAMS, now=NOW)
    assert signal.entry is False
    assert signal.reason == "window_invalid"


def test_lookback_days_covered_reported():
    """시그널이 실제 커버 일수를 보고한다 (lookback_days_at_buy 기록용)."""
    signal = evaluate_bottom_fisher(baseline_series(0.20), 0.18, PARAMS, now=NOW)
    assert signal.lookback_days_covered is not None
    assert abs(signal.lookback_days_covered - 19.0) < 1e-6


def test_ten_day_hourly_history_is_not_treated_as_twenty_day_low():
    """포인트가 많아도 10일 span이면 20일 규칙의 증거가 아니다."""
    series = make_series([
        (hours_ago, 0.20)
        for hours_ago in range(240, -1, -1)
    ])
    signal = evaluate_bottom_fisher(series, 0.18, PARAMS, now=NOW)
    assert signal.entry is False
    assert signal.reason == "window_invalid"


def test_nineteen_day_boundary_is_valid_hourly_approximation():
    signal = evaluate_bottom_fisher(baseline_series(0.20), 0.18, PARAMS, now=NOW)
    assert signal.entry is True
    assert signal.lookback_days_covered == 19.0


def test_merge_price_series_db_wins_on_same_minute():
    """DB 스냅샷과 백필이 같은 분에 겹치면 DB가 우선."""
    ts = NOW - timedelta(hours=1)
    db = [PricePoint(ts, 0.30)]
    backfill = [PricePoint(ts.replace(second=30), 0.99), PricePoint(NOW, 0.31)]
    merged = merge_price_series(db, backfill)
    assert len(merged) == 2
    assert merged[0].price == 0.30


# --- 청산 케이스 ---

def test_exit_calendar_is_primary_path():
    """calendar exit: 보유 120h 도달 → 손익 무관 무조건 청산 (주 경로).

    TP 조건(+30% 이상)이 동시에 성립해도 max_holding이 우선한다 -
    백테스트의 'Y=5일 후 청산' 규칙 복제가 본질이므로.
    """
    should, reason = evaluate_exit(
        buy_price=0.20, current_price=0.30,  # +50% (TP 조건도 성립)
        take_profit_percent=0.30, stop_loss_percent=-0.30,
        holding_hours=120.0, hold_hours=120.0,
        hours_left=500.0, exit_hours=24.0,
    )
    assert should is True
    assert reason == "max_holding"


def test_exit_calendar_boundary_just_before():
    """보유 119.9h < 120h → calendar exit 미발동 (손익 변화 없으면 hold)."""
    should, reason = evaluate_exit(
        buy_price=0.20, current_price=0.21,
        take_profit_percent=0.30, stop_loss_percent=-0.30,
        holding_hours=119.9, hold_hours=120.0,
        hours_left=500.0, exit_hours=24.0,
    )
    assert should is False
    assert reason == "hold"


def test_exit_stop_loss_boundary():
    """손절 안전판: P&L이 정확히 -30%면 청산 (<=)."""
    should, reason = evaluate_exit(
        buy_price=0.20, current_price=0.14,  # -30%
        take_profit_percent=0.30, stop_loss_percent=-0.30,
        holding_hours=10.0, hold_hours=120.0,
        hours_left=500.0, exit_hours=24.0,
    )
    assert should is True
    assert reason == "stop_loss"


def test_exit_take_profit_boundary():
    """익절 안전판: 현재가가 정확히 목표가(0.20*1.30=0.26)면 청산 (>=)."""
    should, reason = evaluate_exit(
        buy_price=0.20, current_price=0.26,
        take_profit_percent=0.30, stop_loss_percent=-0.30,
        holding_hours=10.0, hold_hours=120.0,
        hours_left=500.0, exit_hours=24.0,
    )
    assert should is True
    assert reason == "take_profit"


def test_exit_time_based():
    """time exit: 해결까지 24h 미만이면 청산."""
    should, reason = evaluate_exit(
        buy_price=0.20, current_price=0.21,
        take_profit_percent=0.30, stop_loss_percent=-0.30,
        holding_hours=10.0, hold_hours=120.0,
        hours_left=23.9, exit_hours=24.0,
    )
    assert should is True
    assert reason == "time_exit"


def test_exit_hold_otherwise():
    """모든 조건 미충족 → 보유 유지."""
    should, reason = evaluate_exit(
        buy_price=0.20, current_price=0.22,  # +10%
        take_profit_percent=0.30, stop_loss_percent=-0.30,
        holding_hours=48.0, hold_hours=120.0,
        hours_left=500.0, exit_hours=24.0,
    )
    assert should is False
    assert reason == "hold"


def test_take_profit_target_capped_at_099():
    """TP 목표가 캡: buy*(1+tp) > 0.99면 0.99로 캡 (§3.5 공통 규칙)."""
    assert capped_take_profit_target(0.90, 0.30) == 0.99
    assert abs(capped_take_profit_target(0.20, 0.30) - 0.26) < EPSILON
