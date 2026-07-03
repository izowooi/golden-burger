"""Fear Spike Fade 진입/청산 시그널 테스트 (합성 스냅샷 fixture 기반)."""
from datetime import datetime, timedelta

import pytest

from polybot.strategy.signals import (
    PricePoint,
    SignalParams,
    compute_base,
    evaluate_entry,
    evaluate_exit,
    find_spike_start,
    is_spike_stalled,
    retrace_target_price,
    spike_peak_price,
    take_profit_target,
    volume_multiple,
)

NOW = datetime(2026, 7, 3, 12, 0, 0)
PARAMS = SignalParams()  # 기본값: base<=0.15, jump>=0.10, yes<=0.30, 90m/45m, x2.0


def spike_history(
    base_prob=0.08,
    spike_prob=0.22,
    spike_hours_ago=3.0,
    base_volume=1000.0,
    days=7,
    step_hours=4.0,
):
    """평시 base_prob → spike_hours_ago 전부터 spike_prob인 7일 히스토리.

    스파이크 이후 구간은 15분 간격으로 촘촘하게 생성 (스톨 판정용).
    """
    points = []
    h = days * 24.0
    while h > spike_hours_ago:
        points.append(PricePoint(
            timestamp=NOW - timedelta(hours=h),
            probability=base_prob,
            volume_24h=base_volume,
        ))
        h -= step_hours
    # 스파이크 구간: spike_hours_ago 전부터 현재까지 15분 간격 평탄한 고원
    m = spike_hours_ago * 60.0
    while m >= 0:
        points.append(PricePoint(
            timestamp=NOW - timedelta(minutes=m),
            probability=spike_prob,
            volume_24h=base_volume,
        ))
        m -= 15.0
    return points


def entry(**kwargs):
    """evaluate_entry 호출 헬퍼 (기본값: 진입 성공 케이스)."""
    defaults = dict(
        yes_price=0.22,
        liquidity=20000.0,
        volume_24h=2000.0,  # base_volume 1000의 2배
        hours_left=200.0,
        snapshots=spike_history(),
        params=PARAMS,
        now=NOW,
    )
    defaults.update(kwargs)
    return evaluate_entry(**defaults)


class TestEntrySignal:
    # --- 케이스 1: 정상 진입 (스파이크 3h 경과 + 스톨 + 거래량 2배) ---
    def test_entry_ok(self):
        signal = entry()
        assert signal.entry is True
        assert signal.reason.startswith("fear_spike_fade")
        assert signal.base_price == pytest.approx(0.08)
        assert signal.spike_peak == pytest.approx(0.22)
        assert signal.spike_age_minutes == pytest.approx(180.0)
        assert signal.vol_mult == pytest.approx(2.0, rel=1e-3)

    # --- 케이스 2-3: base 경계 (base <= 0.15) ---
    def test_entry_at_base_max_boundary(self):
        # base 정확히 0.15 → 허용 (<=), 스파이크 0.15+0.10=0.25
        signal = entry(
            yes_price=0.27,
            snapshots=spike_history(base_prob=0.15, spike_prob=0.27),
        )
        assert signal.entry is True

    def test_rejected_base_too_high(self):
        signal = entry(
            yes_price=0.28,
            snapshots=spike_history(base_prob=0.16, spike_prob=0.28),
        )
        assert signal.entry is False
        assert signal.reason.startswith("base_too_high")

    # --- 케이스 4-5: 점프 경계 (yes_now - base >= 0.10, EPSILON 흡수) ---
    def test_entry_at_jump_min_boundary(self):
        # 0.25 - 0.15 = 0.09999... (float 오차) → EPSILON으로 정확히 0.10 취급
        signal = entry(
            yes_price=0.25,
            snapshots=spike_history(base_prob=0.15, spike_prob=0.25),
        )
        assert signal.entry is True

    def test_rejected_jump_below_min(self):
        signal = entry(
            yes_price=0.17,
            snapshots=spike_history(base_prob=0.08, spike_prob=0.17),
        )
        assert signal.entry is False
        assert signal.reason.startswith("no_spike")

    # --- 케이스 6-7: YES 상한 경계 (yes_now <= 0.30) ---
    def test_entry_at_yes_max_boundary(self):
        signal = entry(
            yes_price=0.30,
            snapshots=spike_history(spike_prob=0.30),
        )
        assert signal.entry is True

    def test_rejected_yes_above_max(self):
        signal = entry(
            yes_price=0.31,
            snapshots=spike_history(spike_prob=0.31),
        )
        assert signal.entry is False
        assert signal.reason.startswith("yes_too_high")

    # --- 케이스 8-9: 스파이크 경과 시간 (>= 90분) ---
    def test_rejected_spike_too_fresh(self):
        signal = entry(snapshots=spike_history(spike_hours_ago=1.0))  # 60분
        assert signal.entry is False
        assert signal.reason.startswith("spike_too_fresh")

    def test_entry_at_spike_wait_boundary(self):
        signal = entry(snapshots=spike_history(spike_hours_ago=1.5))  # 정확히 90분
        assert signal.entry is True

    def test_rejected_no_snapshot_crossed_threshold(self):
        # 스냅샷은 전부 평시 가격, 현재가만 스파이크 = 방금 시작
        signal = entry(
            yes_price=0.22,
            snapshots=spike_history(spike_prob=0.08, spike_hours_ago=0.25),
        )
        assert signal.entry is False
        assert signal.reason.startswith("spike_too_fresh")

    # --- 케이스 10: 스톨 미확인 (최근 45분 신고가) ---
    def test_rejected_spike_still_running(self):
        # 현재가가 스파이크 고원(0.22)보다 높음 = 신고가 갱신 중
        signal = entry(yes_price=0.24)
        assert signal.entry is False
        assert signal.reason == "spike_still_running"

    def test_rejected_new_high_inside_stall_window(self):
        # 최근 30분 스냅샷이 이전 고점(0.22)을 넘는 신고가
        snapshots = spike_history() + [
            PricePoint(NOW - timedelta(minutes=30), 0.26, 1000.0),
        ]
        signal = entry(yes_price=0.25, snapshots=snapshots)
        assert signal.entry is False
        assert signal.reason == "spike_still_running"

    # --- 케이스 11-12: 거래량 확인 (>= 2.0x) ---
    def test_rejected_volume_below_multiple(self):
        signal = entry(volume_24h=1900.0)  # 1.9x < 2.0x
        assert signal.entry is False
        assert signal.reason.startswith("volume_unconfirmed")

    def test_rejected_volume_missing(self):
        # 백필 전용 히스토리(volume 없음) → 거래량 확인 불가 → 보수적으로 skip
        snapshots = [
            PricePoint(p.timestamp, p.probability, None)
            for p in spike_history()
        ]
        signal = entry(snapshots=snapshots)
        assert signal.entry is False
        assert signal.reason.startswith("volume_unconfirmed")

    # --- 케이스 13-14: 시간 윈도우 (hours_left >= 72) ---
    def test_rejected_too_close_to_resolution(self):
        signal = entry(hours_left=71.9)
        assert signal.entry is False
        assert signal.reason.startswith("too_close_to_resolution")

    def test_entry_at_hours_min_boundary(self):
        signal = entry(hours_left=72.0)
        assert signal.entry is True

    def test_rejected_no_end_date(self):
        signal = entry(hours_left=None)
        assert signal.entry is False
        assert signal.reason == "no_end_date"

    # --- 케이스 15: 유동성 ---
    def test_rejected_low_liquidity(self):
        signal = entry(liquidity=14999.0)
        assert signal.entry is False
        assert signal.reason.startswith("low_liquidity")

    # --- 케이스 16: 윈도우 invalid → 진입 금지 (cold-start 폴백 금지) ---
    def test_rejected_invalid_window(self):
        # 최근 3시간 스냅샷만 존재 (7d 윈도우 커버리지 미달)
        snapshots = [
            PricePoint(NOW - timedelta(minutes=m), 0.22, 1000.0)
            for m in range(0, 180, 15)
        ]
        signal = entry(snapshots=snapshots)
        assert signal.entry is False
        assert signal.reason == "window_invalid"

    def test_rejected_empty_snapshots(self):
        signal = entry(snapshots=[])
        assert signal.entry is False
        assert signal.reason == "window_invalid"

    # --- 케이스 17: base 계산 구간에 포인트 없음 ---
    def test_rejected_base_undefined(self):
        # 커버리지는 유효하지만 최근 6h 이전 구간이 비어 base 계산 불가
        # (min_coverage 0.5*168h=84h를 넘기기 위해 5일 전 + 최근 5h 구성은 불가능하므로
        #  6h 이전 포인트가 전무한 경우는 윈도우 자체가 최근에 몰림 → window_invalid로 걸림.
        #  base_undefined는 방어 코드지만 직접 검증한다.)
        window = [
            PricePoint(NOW - timedelta(hours=h), 0.10, 1000.0)
            for h in (5.0, 4.0, 3.0, 2.0, 1.0)
        ]
        assert compute_base(window, now=NOW, exclude_recent_hours=6.0) is None


class TestHelpers:
    def test_compute_base_median_excludes_recent(self):
        window = [
            PricePoint(NOW - timedelta(hours=48), 0.06),
            PricePoint(NOW - timedelta(hours=24), 0.08),
            PricePoint(NOW - timedelta(hours=12), 0.10),
            PricePoint(NOW - timedelta(hours=1), 0.30),  # 최근 6h - 제외
        ]
        assert compute_base(window, now=NOW) == pytest.approx(0.08)

    def test_compute_base_even_count_averages(self):
        window = [
            PricePoint(NOW - timedelta(hours=48), 0.06),
            PricePoint(NOW - timedelta(hours=24), 0.10),
        ]
        assert compute_base(window, now=NOW) == pytest.approx(0.08)

    def test_find_spike_start_first_crossing(self):
        points = [
            PricePoint(NOW - timedelta(hours=5), 0.08),
            PricePoint(NOW - timedelta(hours=3), 0.19),  # 첫 crossing (>= 0.18)
            PricePoint(NOW - timedelta(hours=1), 0.22),
        ]
        assert find_spike_start(points, threshold=0.18) == NOW - timedelta(hours=3)

    def test_find_spike_start_none_when_no_crossing(self):
        points = [PricePoint(NOW - timedelta(hours=3), 0.08)]
        assert find_spike_start(points, threshold=0.18) is None

    def test_spike_peak_includes_current(self):
        points = [
            PricePoint(NOW - timedelta(hours=3), 0.19),
            PricePoint(NOW - timedelta(hours=2), 0.26),
            PricePoint(NOW - timedelta(hours=1), 0.22),
        ]
        peak = spike_peak_price(points, NOW - timedelta(hours=3), current_price=0.21)
        assert peak == pytest.approx(0.26)

    def test_is_spike_stalled_no_prior_data(self):
        # 스톨 윈도우 이전 데이터 없음 → 판단 불가 → False (보수적)
        points = [PricePoint(NOW - timedelta(minutes=10), 0.22)]
        assert is_spike_stalled(points, 0.22, 45.0, now=NOW) is False

    def test_volume_multiple_ignores_none(self):
        points = [
            PricePoint(NOW - timedelta(hours=2), 0.2, 1000.0),
            PricePoint(NOW - timedelta(hours=1), 0.2, None),  # 백필 포인트
        ]
        assert volume_multiple(points, 3000.0) == pytest.approx(3.0)

    def test_volume_multiple_none_without_data(self):
        points = [PricePoint(NOW - timedelta(hours=1), 0.2, None)]
        assert volume_multiple(points, 3000.0) is None


class TestExitSignal:
    def exit(self, **kwargs):
        # NO 0.78 매수 (YES 0.22), base 0.08, peak 0.22
        # retrace 목표 YES = 0.08 + 0.5*(0.22-0.08) = 0.15
        defaults = dict(
            buy_price=0.78,
            current_price=0.80,
            current_yes_price=0.20,
            base_price=0.08,
            spike_peak=0.22,
            retrace_ratio=0.5,
            holding_hours=10.0,
            hours_left=150.0,
            take_profit_percent=0.08,
            stop_loss_percent=-0.10,
            exit_hours=24.0,
            max_holding_hours=72.0,
        )
        defaults.update(kwargs)
        return evaluate_exit(**defaults)

    def test_hold(self):
        signal = self.exit()
        assert signal.should_sell is False
        assert signal.reason == "hold"

    def test_stop_loss(self):
        # NO 0.78 → 0.70 (-10.3%): YES 계속 상승 = 진짜 정보
        signal = self.exit(current_price=0.70, current_yes_price=0.30)
        assert signal.should_sell is True
        assert signal.reason == "stop_loss"

    def test_retrace_target_hit(self):
        # YES 0.15 = 목표 정확히 도달 (<=)
        signal = self.exit(current_yes_price=0.15, current_price=0.85)
        assert signal.should_sell is True
        assert signal.reason == "retrace_target"

    def test_retrace_target_not_hit(self):
        signal = self.exit(current_yes_price=0.16, current_price=0.84)
        assert signal.should_sell is False

    def test_retrace_skipped_without_snapshot(self):
        # 스냅샷 최신 YES 없음 → retrace 판단 보류, 다른 조건으로 폴백
        signal = self.exit(current_yes_price=None)
        assert signal.should_sell is False
        assert signal.reason == "hold"

    def test_retrace_skipped_with_bad_peak(self):
        # peak <= base (데이터 이상) → retrace 판정 skip
        signal = self.exit(spike_peak=0.08, current_yes_price=0.01)
        assert signal.should_sell is False

    def test_take_profit_secondary(self):
        # 목표가 0.78 * 1.08 = 0.8424 (retrace 미도달 상태에서 NO만 상승)
        signal = self.exit(current_price=0.85, current_yes_price=0.16)
        assert signal.should_sell is True
        assert signal.reason == "take_profit"

    def test_take_profit_capped_at_099(self):
        # NO 0.93 매수 → 목표 1.0044는 도달 불가 → 0.99 캡에서 익절
        assert take_profit_target(0.93, 0.08) == pytest.approx(0.99)
        signal = self.exit(
            buy_price=0.93, current_price=0.99, current_yes_price=0.16
        )
        assert signal.should_sell is True
        assert signal.reason == "take_profit"

    def test_max_holding(self):
        signal = self.exit(holding_hours=72.0)
        assert signal.should_sell is True
        assert signal.reason == "max_holding"

    def test_time_exit(self):
        signal = self.exit(hours_left=23.5)
        assert signal.should_sell is True
        assert signal.reason == "time_exit"

    def test_stop_loss_priority_over_retrace(self):
        # 손절과 retrace가 동시 성립하면 손절이 먼저 (사유 정확성)
        signal = self.exit(current_price=0.65, current_yes_price=0.10)
        assert signal.reason == "stop_loss"

    def test_retrace_priority_over_max_holding(self):
        signal = self.exit(current_yes_price=0.10, holding_hours=80.0)
        assert signal.reason == "retrace_target"

    def test_hold_when_no_end_date(self):
        signal = self.exit(hours_left=None)
        assert signal.should_sell is False

    def test_retrace_target_price_formula(self):
        assert retrace_target_price(0.08, 0.22, 0.5) == pytest.approx(0.15)
