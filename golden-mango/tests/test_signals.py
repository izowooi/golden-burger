"""Patience Premium 진입/청산 시그널 테스트 (합성 스냅샷 fixture).

핵심 수식: y = ((1 - p) / p) * (8760 / hours_left), 진입 <=> y >= y_min.
"""
from datetime import datetime, timedelta

import pytest

from polybot.strategy.signals import (
    Snap,
    carry_yield,
    check_carry_entry,
    check_momentum_gate,
    evaluate_entry,
    evaluate_exit,
    favorite_price_change,
    take_profit_target,
)

NOW = datetime(2026, 7, 3, 12, 0, 0)

# 스펙 기본값
YIELD_MIN = 2.0
PROB_MIN = 0.85
PROB_MAX = 0.985
ENTRY_HOURS_MIN = 6
ENTRY_HOURS_MAX = 336
LOOKBACK = 6
MIN_CHANGE = -0.02


def make_window(start_price, end_price, points=7, span_hours=6.0, now=NOW):
    """균등 간격 합성 스냅샷 (YES 가격 기준, 시간 오름차순).

    첫/마지막 포인트는 start/end 값을 정확히 갖는다 (경계 테스트용).
    """
    span = timedelta(hours=span_hours)
    result = []
    for i in range(points):
        frac = i / (points - 1)
        if i == 0:
            price = start_price
        elif i == points - 1:
            price = end_price
        else:
            price = start_price + (end_price - start_price) * frac
        result.append(Snap(now - span + span * frac, price))
    return result


def entry(price, hours_left, snapshots, favorite_index=0, **overrides):
    """기본 파라미터로 evaluate_entry 호출."""
    params = dict(
        yield_min=YIELD_MIN,
        prob_min=PROB_MIN,
        prob_max=PROB_MAX,
        entry_hours_min=ENTRY_HOURS_MIN,
        entry_hours_max=ENTRY_HOURS_MAX,
        momentum_lookback_hours=LOOKBACK,
        momentum_min_change=MIN_CHANGE,
        now=NOW,
    )
    params.update(overrides)
    return evaluate_entry(
        price=price,
        hours_left=hours_left,
        snapshots=snapshots,
        favorite_index=favorite_index,
        **params,
    )


def carry(price, hours_left, **overrides):
    """기본 파라미터로 check_carry_entry 호출."""
    params = dict(
        yield_min=YIELD_MIN,
        prob_min=PROB_MIN,
        prob_max=PROB_MAX,
        entry_hours_min=ENTRY_HOURS_MIN,
        entry_hours_max=ENTRY_HOURS_MAX,
    )
    params.update(overrides)
    return check_carry_entry(price, hours_left, **params)


@pytest.fixture
def flat_window():
    """유효한 6h 윈도우, 가격 변화 0 (모멘텀 가드 통과)."""
    return make_window(0.95, 0.95)


# ---------------------------------------------------------------------------
# 캐리 수익률 수식
# ---------------------------------------------------------------------------

class TestCarryYield:
    def test_formula(self):
        """케이스 1: y = ((1-p)/p) * (8760/h) 수식 그 자체.

        p=0.95, 24h -> (0.05/0.95) * 365 = 19.2105...
        """
        assert carry_yield(0.95, 24.0) == pytest.approx((0.05 / 0.95) * 365)
        assert carry_yield(0.90, 336.0) == pytest.approx((0.1 / 0.9) * (8760 / 336))

    def test_invalid_inputs_return_none(self):
        """p<=0, p>=1, h<=0, h=None은 계산 불가."""
        assert carry_yield(0.0, 24.0) is None
        assert carry_yield(1.0, 24.0) is None
        assert carry_yield(0.95, 0.0) is None
        assert carry_yield(0.95, -1.0) is None
        assert carry_yield(0.95, None) is None


# ---------------------------------------------------------------------------
# 캐리 진입: 시간 창 / 확률 밴드 / 수익률 허들 경계
# ---------------------------------------------------------------------------

class TestCarryEntry:
    def test_basic_entry_ok(self, flat_window):
        """케이스 2: 24h 남은 0.95 favorite -> y=19.2 >= 2.0 -> 진입 O."""
        decision = entry(0.95, 24.0, flat_window)
        assert decision.entry is True
        assert decision.reason.startswith("carry_y")
        assert decision.carry_yield == pytest.approx((0.05 / 0.95) * 365)

    def test_prob_min_boundary_inclusive(self, flat_window):
        """케이스 3: 하한 0.85 포함 O / 0.849 X."""
        assert entry(0.85, 24.0, flat_window).entry is True
        rejected = entry(0.849, 24.0, flat_window)
        assert rejected.entry is False
        assert rejected.reason.startswith("price_out_of_band")

    def test_prob_max_boundary_inclusive(self, flat_window):
        """케이스 4: 상한 0.985 포함 O / 0.986 X (float 오차 EPSILON 흡수)."""
        assert entry(0.985, 24.0, flat_window).entry is True
        rejected = entry(0.986, 24.0, flat_window)
        assert rejected.entry is False
        assert rejected.reason.startswith("price_out_of_band")

    def test_too_late_boundary(self, flat_window):
        """케이스 5: h <= 6 진입 금지 (6.0 X / 6.1 O)."""
        rejected = entry(0.90, 6.0, flat_window)
        assert rejected.entry is False
        assert rejected.reason.startswith("too_late")
        assert entry(0.90, 6.1, flat_window).entry is True

    def test_too_early_boundary(self, flat_window):
        """케이스 6: h > 336 진입 금지 (336.0 O / 336.1 X)."""
        assert entry(0.90, 336.0, flat_window).entry is True
        rejected = entry(0.90, 336.1, flat_window)
        assert rejected.entry is False
        assert rejected.reason.startswith("too_early")

    def test_yield_hurdle_boundary_exact(self):
        """케이스 7: y == y_min 경계 포함 O (>= 조건), 그보다 높은 허들이면 X."""
        y = carry_yield(0.95, 24.0)
        ok, reason, got = carry(0.95, 24.0, yield_min=y)
        assert ok is True
        assert got == pytest.approx(y)

        rejected, reason, _ = carry(0.95, 24.0, yield_min=y + 1e-6)
        assert rejected is False
        assert reason.startswith("yield_below_min")

    def test_yield_below_default_hurdle(self, flat_window):
        """케이스 8: 0.98 @ 336h -> y=0.53 < 2.0 -> 진입 X.

        같은 가격이라도 시간이 짧으면 통과 (0.98 @ 24h -> y=7.45).
        시장이 스스로 진입 frontier를 형성하는 이 전략의 핵심 성질.
        """
        rejected = entry(0.98, 336.0, flat_window)
        assert rejected.entry is False
        assert rejected.reason.startswith("yield_below_min")
        assert rejected.carry_yield == pytest.approx((0.02 / 0.98) * (8760 / 336))

        assert entry(0.98, 24.0, flat_window).entry is True

    def test_no_end_date_and_resolved(self, flat_window):
        """케이스 9: endDate 없음/이미 해결 -> 진입 금지."""
        assert entry(0.95, None, flat_window).reason == "no_end_date"
        assert entry(0.95, -1.0, flat_window).reason == "already_resolved"


# ---------------------------------------------------------------------------
# 모멘텀 가드: 급락 중 진입 금지
# ---------------------------------------------------------------------------

class TestMomentumGuard:
    def test_falling_favorite_rejected(self):
        """케이스 10: favorite(YES) 6h간 -0.03 하락 -> 진입 X."""
        window = make_window(0.95, 0.92)
        decision = entry(0.92, 24.0, window, favorite_index=0)
        assert decision.entry is False
        assert decision.reason.startswith("momentum_down")
        assert decision.momentum_change == pytest.approx(-0.03)

    def test_min_change_boundary_inclusive(self):
        """케이스 11: 변화 == min_change 경계 포함 O (EPSILON 적용).

        부동소수점 오차를 피하기 위해 2진수로 정확히 표현되는 값 사용.
        """
        window = make_window(0.9375, 0.9375 - 0.015625)  # 변화 정확히 -0.015625
        ok, _, change = check_momentum_gate(window, 0, min_change=-0.015625)
        assert ok is True
        assert change == -0.015625

        # 같은 윈도우, 더 엄격한 게이트(-0.015) -> 탈락
        rejected, reason, _ = check_momentum_gate(window, 0, min_change=-0.015)
        assert rejected is False
        assert reason.startswith("momentum_down")

    def test_near_default_gate(self):
        """기본 가드(-0.02) 기준: -0.021 하락은 X, -0.019 하락은 O."""
        assert entry(0.90, 24.0, make_window(0.921, 0.90)).entry is False
        assert entry(0.90, 24.0, make_window(0.919, 0.90)).entry is True

    def test_no_favorite_sign_flip(self):
        """케이스 12: favorite이 NO면 YES 하락 = NO 상승 -> 진입 O, 반대는 X."""
        # YES 0.10 -> 0.07 (NO favorite 상승 +0.03): NO 가격 0.93
        yes_falling = make_window(0.10, 0.07)
        ok = entry(0.93, 24.0, yes_falling, favorite_index=1)
        assert ok.entry is True
        assert ok.momentum_change == pytest.approx(0.03)

        # YES 0.05 -> 0.08 (NO favorite 하락 -0.03)
        yes_rising = make_window(0.05, 0.08)
        rejected = entry(0.92, 24.0, yes_rising, favorite_index=1)
        assert rejected.entry is False
        assert rejected.reason.startswith("momentum_down")

    def test_favorite_price_change_requires_two_points(self):
        assert favorite_price_change([], 0) is None
        assert favorite_price_change([Snap(NOW, 0.9)], 0) is None


# ---------------------------------------------------------------------------
# 윈도우 유효성: invalid면 진입 금지 (관대한 cold-start 폴백 금지)
# ---------------------------------------------------------------------------

class TestWindowGating:
    def test_insufficient_points_rejected(self):
        """케이스 13: 스냅샷 2개뿐 -> window_invalid -> 진입 X."""
        window = make_window(0.95, 0.95, points=2)
        decision = entry(0.95, 24.0, window)
        assert decision.entry is False
        assert decision.reason == "window_invalid"

    def test_empty_snapshots_rejected(self):
        """케이스 14: 스냅샷 없음 -> 진입 X."""
        decision = entry(0.95, 24.0, [])
        assert decision.entry is False
        assert decision.reason == "window_invalid"

    def test_low_coverage_rejected(self):
        """케이스 15: 포인트는 많지만 최근 1h에 몰림 (커버리지 < 3h) -> 진입 X."""
        window = make_window(0.95, 0.95, points=10, span_hours=1.0)
        decision = entry(0.95, 24.0, window)
        assert decision.entry is False
        assert decision.reason == "window_invalid"

    def test_carry_checked_before_window(self):
        """캐리 탈락이 윈도우 검사보다 먼저 보고된다."""
        decision = entry(0.99, 24.0, [])
        assert decision.reason.startswith("price_out_of_band")


# ---------------------------------------------------------------------------
# 청산 판정 (SL -6% -> TP 0.99 도달 -> time exit 2h 전, trailing 없음)
# ---------------------------------------------------------------------------

class TestExitSignals:
    EXIT_KWARGS = dict(
        stop_loss_percent=-0.06,
        take_profit_percent=9.99,
        exit_hours=2,
    )

    def test_stop_loss(self):
        """케이스 16: buy 0.90 -> 0.84 = -6.7% -> stop_loss."""
        reason = evaluate_exit(0.90, 0.84, 100.0, **self.EXIT_KWARGS)
        assert reason == "stop_loss"

    def test_stop_loss_boundary_exact(self):
        """P&L 정확히 -6%는 손절 (<= 조건, EPSILON 적용). -5.9%는 보유."""
        assert evaluate_exit(0.90, 0.90 * 0.94, 100.0, **self.EXIT_KWARGS) == "stop_loss"
        assert evaluate_exit(0.90, 0.847, 100.0, **self.EXIT_KWARGS) is None

    def test_take_profit_at_099(self):
        """케이스 17: 목표가 = min(buy*(1+9.99), 0.99) = 0.99. 0.99 도달 = 익절."""
        assert take_profit_target(0.90, 9.99) == pytest.approx(0.99)
        assert evaluate_exit(0.90, 0.99, 100.0, **self.EXIT_KWARGS) == "take_profit"
        # 0.989는 아직 수렴 미완 -> 보유
        assert evaluate_exit(0.90, 0.989, 100.0, **self.EXIT_KWARGS) is None

    def test_take_profit_target_cap_generic(self):
        """tp를 낮게 오버라이드해도 0.99 캡은 유지된다."""
        assert take_profit_target(0.50, 0.30) == pytest.approx(0.65)
        assert take_profit_target(0.95, 0.30) == pytest.approx(0.99)

    def test_time_exit(self):
        """케이스 18: 해결 1.5h 전 -> time_exit. 2.0h는 아직 보유 (< 조건)."""
        assert evaluate_exit(0.90, 0.91, 1.5, **self.EXIT_KWARGS) == "time_exit"
        assert evaluate_exit(0.90, 0.91, 2.0, **self.EXIT_KWARGS) is None

    def test_no_trailing_stop(self):
        """케이스 19: 고점 반납해도 SL 전까지 보유 (trailing 없음이 의도).

        buy 0.90 -> 고점 0.97 -> 0.92 (고점 대비 -5.2%지만 P&L +2.2%) -> 보유.
        """
        reason = evaluate_exit(0.90, 0.92, 100.0, **self.EXIT_KWARGS)
        assert reason is None

    def test_stop_loss_priority_over_time_exit(self):
        """SL과 time exit 동시 충족 -> SL 우선."""
        reason = evaluate_exit(0.90, 0.80, 1.5, **self.EXIT_KWARGS)
        assert reason == "stop_loss"

    def test_no_end_date_never_time_exits(self):
        reason = evaluate_exit(0.90, 0.91, None, **self.EXIT_KWARGS)
        assert reason is None
