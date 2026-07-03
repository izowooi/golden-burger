"""Cascade Rider 진입/청산 시그널 테스트 (합성 스냅샷 fixture)."""
from datetime import datetime, timedelta

import pytest

from polybot.strategy.signals import (
    SnapshotPoint,
    evaluate_entry,
    is_drift_dead,
    take_profit_target,
)

NOW = datetime(2026, 7, 1, 12, 0, 0)

# 12개 포인트: 23h ~ 1h 전, 2시간 간격 (4h 버킷 6개에 각 2개씩)
HOURS_AGO = [23, 21, 19, 17, 15, 13, 11, 9, 7, 5, 3, 1]


def make_points(prices, vols=10000.0, hours_ago=HOURS_AGO):
    """가격 리스트(오래된 것 먼저)로 합성 스냅샷 생성."""
    if not isinstance(vols, list):
        vols = [vols] * len(prices)
    return [
        SnapshotPoint(
            timestamp=NOW - timedelta(hours=h),
            probability=p,
            volume_24h=v,
        )
        for h, p, v in zip(hours_ago, prices, vols)
    ]


def linear_prices(start: float, stop: float, n: int = 12):
    """start -> stop 선형 증가 가격 (모든 버킷 변화 비음)."""
    step = (stop - start) / (n - 1)
    return [round(start + step * i, 6) for i in range(n)]


UPTREND = make_points(linear_prices(0.55, 0.605))       # YES 상승 드리프트
DOWNTREND = make_points(linear_prices(0.55, 0.495))     # YES 하락 = NO 상승

# 지그재그: 드리프트는 밴드 안(+0.06)이지만 6버킷 중 3개가 음의 변화 (일관성 0.5)
ZIGZAG = make_points(
    [0.55, 0.54, 0.56, 0.58, 0.58, 0.57, 0.59, 0.61, 0.61, 0.60, 0.60, 0.61]
)


class TestEntrySignal:
    """진입 O/X 경계 케이스."""

    def test_o_uptrend_buys_yes(self):
        """케이스 1 (O): 일관된 상승 드리프트 + 거래량 가속 -> YES 매수."""
        decision = evaluate_entry(UPTREND, yes_price=0.61, volume_24h=13000, now=NOW)
        assert decision.should_enter is True
        assert decision.side == "Yes"
        assert decision.reason == "cascade_up"
        assert decision.drift == pytest.approx(0.06)
        assert decision.consistency == pytest.approx(1.0)
        assert decision.vol_accel == pytest.approx(1.3)

    def test_o_downtrend_buys_no(self):
        """케이스 2 (O): YES 하락 드리프트 -> NO 토큰 매수 (밴드는 NO 기준)."""
        decision = evaluate_entry(DOWNTREND, yes_price=0.49, volume_24h=13000, now=NOW)
        assert decision.should_enter is True
        assert decision.side == "No"
        assert decision.reason == "cascade_down"
        assert decision.token_price == pytest.approx(0.51)
        assert decision.drift == pytest.approx(0.06)

    def test_x_drift_below_min(self):
        """케이스 3 (X): 드리프트 +0.03 < 하한 0.04."""
        points = make_points(linear_prices(0.55, 0.578))
        decision = evaluate_entry(points, yes_price=0.58, volume_24h=13000, now=NOW)
        assert decision.should_enter is False
        assert decision.reason.startswith("drift_too_small")

    def test_x_drift_above_max(self):
        """케이스 4 (X): 드리프트 +0.12 > 상한 0.10 (mean-revert 영역)."""
        points = make_points(linear_prices(0.50, 0.61))
        decision = evaluate_entry(points, yes_price=0.62, volume_24h=13000, now=NOW)
        assert decision.should_enter is False
        assert decision.reason.startswith("drift_too_large")

    def test_o_drift_at_min_boundary(self):
        """케이스 5 (O): 드리프트 정확히 +0.04 (하한 포함)."""
        points = make_points(linear_prices(0.55, 0.585))
        decision = evaluate_entry(points, yes_price=0.59, volume_24h=13000, now=NOW)
        assert decision.should_enter is True
        assert decision.drift == pytest.approx(0.04)

    def test_x_price_above_band(self):
        """케이스 6 (X): 매수 토큰 가격 0.85 > 상한 0.80 (러닝룸 없음)."""
        points = make_points(linear_prices(0.79, 0.845))
        decision = evaluate_entry(points, yes_price=0.85, volume_24h=13000, now=NOW)
        assert decision.should_enter is False
        assert decision.reason.startswith("price_out_of_band")

    def test_o_price_at_max_boundary(self):
        """케이스 7 (O): 가격 정확히 0.80 (상한 포함)."""
        points = make_points(linear_prices(0.745, 0.795))
        decision = evaluate_entry(points, yes_price=0.80, volume_24h=13000, now=NOW)
        assert decision.should_enter is True
        assert decision.token_price == pytest.approx(0.80)

    def test_x_price_below_band(self):
        """케이스 8 (X): 매수 토큰 가격 0.35 < 하한 0.40."""
        points = make_points(linear_prices(0.295, 0.345))
        decision = evaluate_entry(points, yes_price=0.35, volume_24h=13000, now=NOW)
        assert decision.should_enter is False
        assert decision.reason.startswith("price_out_of_band")

    def test_x_inconsistent_buckets(self):
        """케이스 9 (X): 드리프트는 밴드 안이지만 버킷 일관성 0.5 < 0.70."""
        decision = evaluate_entry(ZIGZAG, yes_price=0.61, volume_24h=13000, now=NOW)
        assert decision.should_enter is False
        assert decision.reason.startswith("inconsistent_drift")

    def test_x_volume_accel_too_low(self):
        """케이스 10 (X): 거래량 가속 1.1x < 1.2x."""
        decision = evaluate_entry(UPTREND, yes_price=0.61, volume_24h=11000, now=NOW)
        assert decision.should_enter is False
        assert decision.reason.startswith("vol_accel_too_low")

    def test_o_volume_accel_at_boundary(self):
        """케이스 11 (O): 거래량 가속 정확히 1.2x (하한 포함)."""
        decision = evaluate_entry(UPTREND, yes_price=0.61, volume_24h=12000, now=NOW)
        assert decision.should_enter is True
        assert decision.vol_accel == pytest.approx(1.2)

    def test_x_window_too_few_points(self):
        """케이스 12 (X): 스냅샷 3개 < min_points 5 -> 진입 금지.

        banana의 관대한 cold-start 폴백('그냥 진입')을 금지한 것을 검증.
        """
        points = make_points([0.55, 0.58, 0.60], hours_ago=[20, 10, 1])
        decision = evaluate_entry(points, yes_price=0.61, volume_24h=13000, now=NOW)
        assert decision.should_enter is False
        assert decision.reason == "window_invalid"

    def test_x_window_insufficient_coverage(self):
        """케이스 13 (X): 포인트 수는 충분하나 최근 3시간에 몰림 (커버리지 < 50%).

        banana라면 count 기반 윈도우로 통과했을 케이스 - timestamp 검증으로 차단.
        """
        points = make_points(
            linear_prices(0.55, 0.605), hours_ago=[3.0, 2.75, 2.5, 2.25, 2.0, 1.75, 1.5, 1.25, 1.0, 0.75, 0.5, 0.25]
        )
        decision = evaluate_entry(points, yes_price=0.61, volume_24h=13000, now=NOW)
        assert decision.should_enter is False
        assert decision.reason == "window_invalid"

    def test_x_no_volume_data(self):
        """케이스 14 (X): 윈도우에 거래량 데이터 없음 -> 가속 판정 불가."""
        points = make_points(linear_prices(0.55, 0.605), vols=[None] * 12)
        decision = evaluate_entry(points, yes_price=0.61, volume_24h=13000, now=NOW)
        assert decision.should_enter is False
        assert decision.reason == "no_volume_data"

    def test_x_flat_no_drift(self):
        """케이스 15 (X): 가격 변화 없음 -> 방향 판정 불가."""
        points = make_points([0.55] * 12)
        decision = evaluate_entry(points, yes_price=0.55, volume_24h=13000, now=NOW)
        assert decision.should_enter is False
        assert decision.reason == "no_drift"

    def test_o_drift_at_max_boundary(self):
        """케이스 16 (O): 드리프트 정확히 +0.10 (상한 포함 - mean-revert 배제는 초과부터)."""
        points = make_points(linear_prices(0.55, 0.645))
        decision = evaluate_entry(points, yes_price=0.65, volume_24h=13000, now=NOW)
        assert decision.should_enter is True
        assert decision.drift == pytest.approx(0.10)

    def test_o_price_at_min_boundary(self):
        """케이스 17 (O): 매수 토큰 가격 정확히 0.40 (하한 포함)."""
        points = make_points(linear_prices(0.345, 0.395))
        decision = evaluate_entry(points, yes_price=0.40, volume_24h=13000, now=NOW)
        assert decision.should_enter is True
        assert decision.token_price == pytest.approx(0.40)

    def test_o_consistency_five_of_six_buckets(self):
        """케이스 18 (O): 6버킷 중 5개 비음 (5/6 = 0.83 >= 0.70)."""
        points = make_points(
            [0.55, 0.57, 0.57, 0.58, 0.58, 0.57, 0.57, 0.58, 0.58, 0.59, 0.59, 0.60]
        )
        decision = evaluate_entry(points, yes_price=0.61, volume_24h=13000, now=NOW)
        assert decision.should_enter is True
        assert decision.consistency == pytest.approx(5 / 6)

    def test_x_consistency_four_of_six_buckets(self):
        """케이스 19 (X): 6버킷 중 4개만 비음 (4/6 = 0.67 < 0.70) -> 경계 바로 아래."""
        points = make_points(
            [0.55, 0.57, 0.57, 0.56, 0.57, 0.56, 0.57, 0.58, 0.58, 0.59, 0.59, 0.60]
        )
        decision = evaluate_entry(points, yes_price=0.61, volume_24h=13000, now=NOW)
        assert decision.should_enter is False
        assert decision.reason.startswith("inconsistent_drift")
        assert decision.consistency == pytest.approx(4 / 6)


class TestDriftDeath:
    """청산: 드리프트 소멸 판정."""

    def test_dead_when_flat_or_falling(self):
        points = [
            SnapshotPoint(NOW - timedelta(hours=5), 0.62),
            SnapshotPoint(NOW - timedelta(hours=3), 0.61),
            SnapshotPoint(NOW - timedelta(hours=1), 0.60),
        ]
        assert is_drift_dead(points, token_index=0, death_window_hours=6, now=NOW) is True

    def test_alive_when_rising(self):
        points = [
            SnapshotPoint(NOW - timedelta(hours=5), 0.60),
            SnapshotPoint(NOW - timedelta(hours=1), 0.63),
        ]
        assert is_drift_dead(points, token_index=0, death_window_hours=6, now=NOW) is False

    def test_no_side_inverts_prices(self):
        # YES 상승 = NO 하락 -> NO 포지션 기준으로는 드리프트 소멸
        points = [
            SnapshotPoint(NOW - timedelta(hours=5), 0.40),
            SnapshotPoint(NOW - timedelta(hours=1), 0.45),
        ]
        assert is_drift_dead(points, token_index=1, death_window_hours=6, now=NOW) is True

    def test_insufficient_data_returns_none(self):
        points = [SnapshotPoint(NOW - timedelta(hours=1), 0.60)]
        assert is_drift_dead(points, token_index=0, death_window_hours=6, now=NOW) is None


class TestTakeProfitTarget:
    """익절 목표가 0.99 캡 (TP 도달 불가 버그 수정)."""

    def test_normal_target(self):
        assert take_profit_target(0.60, 0.15) == pytest.approx(0.69)

    def test_capped_at_099(self):
        # 0.90 * 1.15 = 1.035 > 0.99 -> 캡
        assert take_profit_target(0.90, 0.15) == pytest.approx(0.99)

    def test_exactly_at_cap(self):
        assert take_profit_target(0.99, 0.15) == pytest.approx(0.99)
