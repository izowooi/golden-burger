"""시간 윈도우 검증 로직 테스트 (banana의 개수 기반 윈도우 버그 수정 검증)."""
from datetime import datetime, timedelta

from polybot.strategy.signals import PricePoint, get_window, is_window_valid

NOW = datetime(2026, 7, 1, 12, 0, 0)


def points_at_hours(hours, price=0.5):
    return [PricePoint(NOW - timedelta(hours=h), price) for h in hours]


class TestGetWindow:
    def test_filters_by_timestamp(self):
        """hours_back 밖의 스냅샷은 제외된다."""
        snapshots = points_at_hours([72.0, 50.0, 47.9, 24.0, 1.0])
        window = get_window(snapshots, hours_back=48.0, now=NOW)
        assert len(window) == 3
        assert all(p.timestamp >= NOW - timedelta(hours=48) for p in window)

    def test_inclusive_boundary(self):
        """정확히 hours_back 전 스냅샷은 포함(>=)."""
        snapshots = points_at_hours([48.0, 10.0])
        window = get_window(snapshots, hours_back=48.0, now=NOW)
        assert len(window) == 2

    def test_sorted_oldest_first(self):
        """입력 순서와 무관하게 오래된 것 먼저 정렬."""
        snapshots = points_at_hours([1.0, 30.0, 10.0])
        window = get_window(snapshots, hours_back=48.0, now=NOW)
        timestamps = [p.timestamp for p in window]
        assert timestamps == sorted(timestamps)

    def test_empty_input(self):
        assert get_window([], hours_back=48.0, now=NOW) == []

    def test_default_now(self):
        """now 생략 시 utcnow 기준으로 동작."""
        snapshots = [PricePoint(datetime.utcnow() - timedelta(hours=1), 0.5)]
        window = get_window(snapshots, hours_back=48.0)
        assert len(window) == 1


class TestIsWindowValid:
    def test_valid_window(self):
        """포인트 5개 이상 + 커버리지 >= 50% -> valid."""
        window = get_window(
            points_at_hours([47.0, 36.0, 24.0, 12.0, 1.0]), 48.0, NOW
        )
        assert is_window_valid(window, hours_back=48.0) is True

    def test_too_few_points(self):
        """포인트 4개 < min_points 5 -> invalid."""
        window = get_window(points_at_hours([47.0, 30.0, 15.0, 1.0]), 48.0, NOW)
        assert is_window_valid(window, hours_back=48.0) is False

    def test_low_coverage(self):
        """포인트는 많아도 span 20h < 48h*0.5 -> invalid.

        Jenkins가 멈췄다 재개된 뒤 최근에만 몰린 스냅샷으로
        장기 윈도우 신호를 계산하는 banana 버그를 막는다.
        """
        window = get_window(
            points_at_hours([20.0, 16.0, 12.0, 8.0, 4.0, 1.0]), 48.0, NOW
        )
        assert is_window_valid(window, hours_back=48.0) is False

    def test_coverage_exact_boundary(self):
        """span 정확히 24h = 48h*0.5 -> valid (>=)."""
        window = get_window(
            points_at_hours([24.0, 18.0, 12.0, 6.0, 0.0]), 48.0, NOW
        )
        assert is_window_valid(window, hours_back=48.0) is True

    def test_empty_window(self):
        assert is_window_valid([], hours_back=48.0) is False

    def test_custom_min_points(self):
        """min_points 파라미터 조정 가능."""
        window = get_window(points_at_hours([40.0, 20.0, 1.0]), 48.0, NOW)
        assert is_window_valid(window, 48.0, min_points=3) is True
        assert is_window_valid(window, 48.0, min_points=4) is False

    def test_custom_min_coverage(self):
        """min_coverage 파라미터 조정 가능."""
        window = get_window(
            points_at_hours([20.0, 15.0, 10.0, 5.0, 1.0]), 48.0, NOW
        )
        # span 19h: 48h*0.3=14.4h 는 통과, 48h*0.5=24h 는 실패
        assert is_window_valid(window, 48.0, min_coverage=0.3) is True
        assert is_window_valid(window, 48.0, min_coverage=0.5) is False
