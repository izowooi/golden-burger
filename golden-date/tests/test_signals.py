"""Conviction Ladder 진입/청산 시그널 테스트 (합성 스냅샷 fixture)."""
from datetime import datetime, timedelta

import pytest

from polybot.strategy.signals import (
    Snap,
    EntryDecision,
    check_ladder_entry,
    check_momentum_gate,
    evaluate_entry,
    evaluate_exit,
    favorite_price_change,
    ladder_band,
    take_profit_target,
)

NOW = datetime(2026, 7, 3, 12, 0, 0)

# 스펙 기본값 사다리: (max_hours, band_min, band_max)
RUNGS = [
    (24.0, 0.80, 0.95),
    (72.0, 0.75, 0.92),
    (168.0, 0.70, 0.88),
]
ENTRY_HOURS_MIN = 6
LOOKBACK = 6
MIN_CHANGE = -0.01


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


def entry(price, hours_left, snapshots, favorite_index=0):
    """기본 파라미터로 evaluate_entry 호출."""
    return evaluate_entry(
        price=price,
        hours_left=hours_left,
        snapshots=snapshots,
        favorite_index=favorite_index,
        entry_hours_min=ENTRY_HOURS_MIN,
        rungs=RUNGS,
        momentum_lookback_hours=LOOKBACK,
        momentum_min_change=MIN_CHANGE,
        now=NOW,
    )


@pytest.fixture
def flat_window():
    """유효한 6h 윈도우, 가격 변화 0 (모멘텀 게이트 통과)."""
    return make_window(0.85, 0.85)


# ---------------------------------------------------------------------------
# 시간 사다리: 진입 O/X 경계 케이스
# ---------------------------------------------------------------------------

class TestLadderEntry:
    def test_band1_ok(self, flat_window):
        """케이스 1: 12h 남음 + 0.85 → 밴드1 진입 O."""
        decision = entry(0.85, 12.0, flat_window)
        assert decision.entry is True
        assert decision.reason.startswith("ladder1_")

    def test_band1_min_boundary_inclusive(self, flat_window):
        """케이스 2: 밴드1 하한 0.80 포함 O / 0.799 X."""
        assert entry(0.80, 12.0, flat_window).entry is True
        rejected = entry(0.799, 12.0, flat_window)
        assert rejected.entry is False
        assert rejected.reason.startswith("price_out_of_band1")

    def test_band1_max_boundary_inclusive(self, flat_window):
        """케이스 3: 밴드1 상한 0.95 포함 O / 0.951 X."""
        assert entry(0.95, 12.0, flat_window).entry is True
        assert entry(0.951, 12.0, flat_window).entry is False

    def test_hour_boundary_band1_to_band2(self, flat_window):
        """케이스 4: h=24는 밴드1 (0.95 O), h=24.5는 밴드2 (0.95 X, 0.92 O)."""
        assert entry(0.95, 24.0, flat_window).entry is True
        assert entry(0.95, 24.5, flat_window).entry is False
        assert entry(0.92, 24.5, flat_window).entry is True

    def test_band3_boundaries(self, flat_window):
        """케이스 5: 밴드3 (72 < h <= 168) → [0.70, 0.88]."""
        assert entry(0.70, 120.0, flat_window).entry is True
        assert entry(0.88, 168.0, flat_window).entry is True  # h 상한 포함
        assert entry(0.89, 120.0, flat_window).entry is False
        assert entry(0.69, 120.0, flat_window).entry is False

    def test_too_late(self, flat_window):
        """케이스 6: h <= 6 은 진입 금지 (경계: 6.0 X / 6.1 O)."""
        rejected = entry(0.85, 6.0, flat_window)
        assert rejected.entry is False
        assert rejected.reason.startswith("too_late")
        assert entry(0.85, 6.1, flat_window).entry is True

    def test_too_early(self, flat_window):
        """케이스 7: h > 168 은 진입 금지."""
        rejected = entry(0.85, 169.0, flat_window)
        assert rejected.entry is False
        assert rejected.reason.startswith("too_early")

    def test_no_end_date_and_resolved(self, flat_window):
        """케이스 8: endDate 없음/이미 해결 → 진입 금지."""
        assert entry(0.85, None, flat_window).reason == "no_end_date"
        assert entry(0.85, -1.0, flat_window).reason == "already_resolved"

    def test_ladder_band_helper(self):
        assert ladder_band(12.0, ENTRY_HOURS_MIN, RUNGS) == (1, 0.80, 0.95)
        assert ladder_band(48.0, ENTRY_HOURS_MIN, RUNGS) == (2, 0.75, 0.92)
        assert ladder_band(168.0, ENTRY_HOURS_MIN, RUNGS) == (3, 0.70, 0.88)
        assert ladder_band(169.0, ENTRY_HOURS_MIN, RUNGS) is None
        assert ladder_band(6.0, ENTRY_HOURS_MIN, RUNGS) is None
        assert ladder_band(None, ENTRY_HOURS_MIN, RUNGS) is None


# ---------------------------------------------------------------------------
# 모멘텀 게이트: 하락 추세 배제
# ---------------------------------------------------------------------------

class TestMomentumGate:
    def test_falling_favorite_rejected(self):
        """케이스 9: favorite(YES) 6h간 -0.02 하락 → 진입 X."""
        window = make_window(0.90, 0.88)
        decision = entry(0.88, 12.0, window, favorite_index=0)
        assert decision.entry is False
        assert decision.reason.startswith("momentum_down")
        assert decision.momentum_change == pytest.approx(-0.02)

    def test_min_change_boundary_inclusive(self):
        """케이스 10: 변화 == min_change는 경계 포함 O (>= 조건).

        부동소수점 오차를 피하기 위해 2진수로 정확히 표현되는 값 사용.
        """
        window = make_window(0.5, 0.5 - 0.0078125)  # 변화 정확히 -0.0078125
        ok, _, change = check_momentum_gate(window, 0, min_change=-0.0078125)
        assert ok is True
        assert change == -0.0078125

        # 같은 윈도우, 더 엄격한 게이트(-0.0078) → 탈락
        rejected, reason, _ = check_momentum_gate(window, 0, min_change=-0.0078)
        assert rejected is False
        assert reason.startswith("momentum_down")

    def test_clearly_falling_rejected_near_default_gate(self):
        """기본 게이트(-0.01) 기준: -0.011 하락은 X, -0.009 하락은 O."""
        assert entry(0.85, 12.0, make_window(0.861, 0.85)).entry is False
        assert entry(0.85, 12.0, make_window(0.859, 0.85)).entry is True

    def test_rising_favorite_ok(self):
        """케이스 11: 상승 중인 favorite → 진입 O."""
        decision = entry(0.85, 12.0, make_window(0.82, 0.85), favorite_index=0)
        assert decision.entry is True
        assert decision.momentum_change == pytest.approx(0.03)

    def test_no_favorite_sign_flip(self):
        """케이스 12: favorite이 NO면 YES 하락 = NO 상승 → 진입 O, 반대는 X."""
        # YES 0.15 → 0.12 (NO favorite 상승): NO 가격 0.88 가정
        yes_falling = make_window(0.15, 0.12)
        ok = entry(0.88, 12.0, yes_falling, favorite_index=1)
        assert ok.entry is True
        assert ok.momentum_change == pytest.approx(0.03)

        # YES 0.10 → 0.13 (NO favorite 하락 -0.03)
        yes_rising = make_window(0.10, 0.13)
        rejected = entry(0.87, 12.0, yes_rising, favorite_index=1)
        assert rejected.entry is False
        assert rejected.reason.startswith("momentum_down")

    def test_favorite_price_change_requires_two_points(self):
        assert favorite_price_change([], 0) is None
        assert favorite_price_change([Snap(NOW, 0.8)], 0) is None

    def test_check_momentum_gate_direct(self):
        window = make_window(0.80, 0.85)
        ok, reason, change = check_momentum_gate(window, 0, MIN_CHANGE)
        assert ok is True
        assert change == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# 윈도우 유효성: invalid면 진입 금지 (관대한 cold-start 폴백 금지)
# ---------------------------------------------------------------------------

class TestWindowGating:
    def test_insufficient_points_rejected(self):
        """케이스 13: 스냅샷 2개뿐 → window_invalid → 진입 X."""
        window = make_window(0.85, 0.85, points=2)
        decision = entry(0.85, 12.0, window)
        assert decision.entry is False
        assert decision.reason == "window_invalid"

    def test_empty_snapshots_rejected(self):
        """케이스 14: 스냅샷 없음 → 진입 X."""
        decision = entry(0.85, 12.0, [])
        assert decision.entry is False
        assert decision.reason == "window_invalid"

    def test_low_coverage_rejected(self):
        """케이스 15: 포인트는 많지만 최근 1h에 몰림 (커버리지 < 3h) → 진입 X."""
        window = make_window(0.85, 0.85, points=10, span_hours=1.0)
        decision = entry(0.85, 12.0, window)
        assert decision.entry is False
        assert decision.reason == "window_invalid"

    def test_ladder_checked_before_window(self, flat_window):
        """사다리 탈락이 윈도우 검사보다 먼저 보고된다."""
        decision = entry(0.99, 12.0, [])
        assert decision.reason.startswith("price_out_of_band")


# ---------------------------------------------------------------------------
# 청산 판정
# ---------------------------------------------------------------------------

class TestExitSignals:
    EXIT_KWARGS = dict(
        stop_loss_percent=-0.08,
        take_profit_percent=0.12,
        trailing_enabled=True,
        trailing_percent=0.05,
        exit_hours=2,
    )

    def test_stop_loss(self):
        # buy 0.80 → 0.73 = -8.75%
        reason = evaluate_exit(0.80, 0.73, 0.80, 20.0, **self.EXIT_KWARGS)
        assert reason == "stop_loss"

    def test_take_profit(self):
        # buy 0.80 → 0.90 = +12.5% (목표가 0.896)
        reason = evaluate_exit(0.80, 0.90, 0.90, 20.0, **self.EXIT_KWARGS)
        assert reason == "take_profit"

    def test_take_profit_cap_at_099(self):
        """§3.5: buy 0.92 → 목표가 min(1.0304, 0.99) = 0.99, 0.99 도달 시 익절."""
        assert take_profit_target(0.92, 0.12) == pytest.approx(0.99)
        reason = evaluate_exit(0.92, 0.99, 0.99, 20.0, **self.EXIT_KWARGS)
        assert reason == "take_profit"

    def test_take_profit_target_no_cap(self):
        assert take_profit_target(0.50, 0.12) == pytest.approx(0.56)

    def test_trailing_stop(self):
        # 최고가 0.88 대비 0.82 = -6.8% < -5% 트레일링
        reason = evaluate_exit(0.80, 0.82, 0.88, 20.0, **self.EXIT_KWARGS)
        assert reason == "trailing_stop"

    def test_time_exit(self):
        # 손익 중립 + 해결 1.5h 전 → time_exit
        reason = evaluate_exit(0.80, 0.80, 0.80, 1.5, **self.EXIT_KWARGS)
        assert reason == "time_exit"

    def test_hold(self):
        reason = evaluate_exit(0.80, 0.81, 0.81, 20.0, **self.EXIT_KWARGS)
        assert reason is None

    def test_stop_loss_priority_over_trailing(self):
        # SL과 트레일링 둘 다 충족 → SL 우선
        reason = evaluate_exit(0.80, 0.72, 0.90, 20.0, **self.EXIT_KWARGS)
        assert reason == "stop_loss"

    def test_trailing_disabled(self):
        kwargs = dict(self.EXIT_KWARGS, trailing_enabled=False)
        reason = evaluate_exit(0.80, 0.82, 0.88, 20.0, **kwargs)
        assert reason is None

    def test_no_end_date_never_time_exits(self):
        reason = evaluate_exit(0.80, 0.80, 0.80, None, **self.EXIT_KWARGS)
        assert reason is None
