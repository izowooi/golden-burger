"""Hope Crusher 진입/청산 시그널 테스트 (합성 스냅샷 fixture 기반)."""
from datetime import datetime, timedelta

import pytest

from polybot.strategy.signals import (
    PricePoint,
    SignalParams,
    evaluate_entry,
    evaluate_exit,
    take_profit_target,
)

NOW = datetime(2026, 7, 3, 12, 0, 0)
PARAMS = SignalParams()  # 기본값: yes 0.05~0.25, 24~240h, liquidity 10000


def flat_history(prob, hours=24, step_hours=2.0):
    """step 간격으로 hours 시간 동안 일정한 YES 가격 스냅샷 생성."""
    points = []
    h = hours
    while h >= 0:
        points.append(PricePoint(timestamp=NOW - timedelta(hours=h), probability=prob))
        h -= step_hours
    return points


def history_with_override(base_prob, overrides):
    """flat 히스토리에 {hours_ago: prob} 오버라이드 적용."""
    points = []
    h = 24.0
    while h >= 0:
        prob = base_prob
        for hours_ago, override_prob in overrides.items():
            if abs(h - hours_ago) < 1e-9:
                prob = override_prob
        points.append(PricePoint(timestamp=NOW - timedelta(hours=h), probability=prob))
        h -= 2.0
    return points


def entry(**kwargs):
    """evaluate_entry 호출 헬퍼 (기본값 채움)."""
    defaults = dict(
        yes_price=0.15,
        liquidity=20000.0,
        volume_24h=5000.0,
        hours_left=100.0,
        snapshots=flat_history(0.15),
        params=PARAMS,
        now=NOW,
    )
    defaults.update(kwargs)
    return evaluate_entry(**defaults)


class TestEntrySignal:
    # --- 케이스 1: 정상 진입 ---
    def test_entry_ok_flat_longshot(self):
        signal = entry()
        assert signal.entry is True
        assert signal.reason.startswith("hope_crusher")

    # --- 케이스 2-3: YES 밴드 경계 밖 ---
    def test_rejected_yes_below_band(self):
        signal = entry(yes_price=0.04, snapshots=flat_history(0.04))
        assert signal.entry is False
        assert signal.reason.startswith("yes_out_of_band")

    def test_rejected_yes_above_band(self):
        signal = entry(yes_price=0.30, snapshots=flat_history(0.30))
        assert signal.entry is False
        assert signal.reason.startswith("yes_out_of_band")

    # --- 케이스 4-5: YES 밴드 경계 포함 ---
    def test_entry_at_yes_min_boundary(self):
        signal = entry(yes_price=0.05, snapshots=flat_history(0.05))
        assert signal.entry is True

    def test_entry_at_yes_max_boundary(self):
        signal = entry(yes_price=0.25, snapshots=flat_history(0.25))
        assert signal.entry is True

    # --- 케이스 6: 유동성 부족 ---
    def test_rejected_low_liquidity(self):
        signal = entry(liquidity=9999.0)
        assert signal.entry is False
        assert signal.reason.startswith("low_liquidity")

    # --- 케이스 7-9: 시간 윈도우 ---
    def test_rejected_too_late(self):
        signal = entry(hours_left=23.9)
        assert signal.entry is False
        assert signal.reason.startswith("too_late")

    def test_entry_at_hours_min_boundary(self):
        signal = entry(hours_left=24.0)
        assert signal.entry is True

    def test_rejected_too_early(self):
        signal = entry(hours_left=241.0)
        assert signal.entry is False
        assert signal.reason.startswith("too_early")

    def test_entry_at_hours_max_boundary(self):
        signal = entry(hours_left=240.0)
        assert signal.entry is True

    def test_rejected_no_end_date(self):
        signal = entry(hours_left=None)
        assert signal.entry is False
        assert signal.reason == "no_end_date"

    # --- 케이스 10-11: 사건 진행 배제 (24h 상승) ---
    def test_rejected_yes_rising_24h(self):
        # 24h 전 0.12 → 현재 0.15 (+0.03 > 0.02) → 뭔가 일어나는 중, skip
        signal = entry(yes_price=0.15, snapshots=flat_history(0.12))
        assert signal.entry is False
        assert signal.reason.startswith("yes_rising_24h")

    def test_entry_allowed_at_rise_threshold(self):
        # 변화 정확히 +0.02 → 허용 (<=)
        signal = entry(yes_price=0.15, snapshots=flat_history(0.13))
        assert signal.entry is True

    # --- 케이스 12: 사건 진행 배제 (6h 급등) ---
    def test_rejected_yes_spike_6h(self):
        # 24h 변화는 0이지만 4h 전 저점 0.10 → 현재 0.15 (급등 +0.05 >= 0.05)
        snapshots = history_with_override(0.15, {4.0: 0.10})
        signal = entry(yes_price=0.15, snapshots=snapshots)
        assert signal.entry is False
        assert signal.reason.startswith("yes_spike_6h")

    def test_entry_allowed_spike_below_threshold(self):
        # 6h 저점 대비 +0.04 < 0.05 → 허용
        snapshots = history_with_override(0.15, {4.0: 0.11})
        signal = entry(yes_price=0.15, snapshots=snapshots)
        assert signal.entry is True

    # --- 케이스 13: 윈도우 invalid → 진입 금지 (cold-start 폴백 금지) ---
    def test_rejected_invalid_window_too_few(self):
        snapshots = flat_history(0.15, hours=1, step_hours=0.5)  # 3개, 1h 커버
        signal = entry(snapshots=snapshots)
        assert signal.entry is False
        assert signal.reason == "window_invalid"

    def test_rejected_invalid_window_low_coverage(self):
        # 포인트는 많지만 전부 최근 2시간에 몰림 (Jenkins 재시작 직후)
        snapshots = flat_history(0.15, hours=2, step_hours=0.25)
        signal = entry(snapshots=snapshots)
        assert signal.entry is False
        assert signal.reason == "window_invalid"

    def test_rejected_empty_snapshots(self):
        signal = entry(snapshots=[])
        assert signal.entry is False
        assert signal.reason == "window_invalid"

    # --- 케이스 14: 거래량 필터 (기본 비활성, 켜면 동작) ---
    def test_volume_filter_disabled_by_default(self):
        signal = entry(volume_24h=0.0)
        assert signal.entry is True

    def test_volume_filter_when_enabled(self):
        params = SignalParams(min_volume_24h=10000.0)
        signal = entry(volume_24h=5000.0, params=params)
        assert signal.entry is False
        assert signal.reason.startswith("low_volume")


class TestExitSignal:
    def exit(self, **kwargs):
        defaults = dict(
            buy_price=0.85,
            current_price=0.86,
            hours_left=50.0,
            take_profit_percent=0.06,
            stop_loss_percent=-0.10,
            exit_hours=2.0,
        )
        defaults.update(kwargs)
        return evaluate_exit(**defaults)

    def test_stop_loss(self):
        # NO 0.85 매수 → 0.76 (-10.6%) : YES 급등 = 사건 발생 신호
        signal = self.exit(current_price=0.76)
        assert signal.should_sell is True
        assert signal.reason == "stop_loss"

    def test_take_profit(self):
        # 목표가 0.85 * 1.06 = 0.901
        signal = self.exit(current_price=0.91)
        assert signal.should_sell is True
        assert signal.reason == "take_profit"

    def test_take_profit_capped_at_099(self):
        # NO 0.95 매수 → 목표가 1.007은 도달 불가 → 0.99 캡에서 익절
        assert take_profit_target(0.95, 0.06) == pytest.approx(0.99)
        signal = self.exit(buy_price=0.95, current_price=0.99)
        assert signal.should_sell is True
        assert signal.reason == "take_profit"

    def test_no_take_profit_below_cap(self):
        signal = self.exit(buy_price=0.95, current_price=0.98)
        assert signal.should_sell is False

    def test_time_exit(self):
        signal = self.exit(current_price=0.86, hours_left=1.5)
        assert signal.should_sell is True
        assert signal.reason == "time_exit"

    def test_hold(self):
        signal = self.exit()
        assert signal.should_sell is False
        assert signal.reason == "hold"

    def test_stop_loss_priority_over_time_exit(self):
        signal = self.exit(current_price=0.70, hours_left=1.0)
        assert signal.reason == "stop_loss"

    def test_hold_when_no_end_date(self):
        signal = self.exit(hours_left=None)
        assert signal.should_sell is False
