"""시간 윈도우 검증 로직 테스트 (banana의 count 기반 윈도우 버그 교정 확인)."""
from datetime import datetime, timedelta

from polybot.strategy.signals import (
    SnapshotPoint,
    get_window,
    is_window_valid,
    merge_snapshots,
)

NOW = datetime(2026, 7, 1, 12, 0, 0)


def make_point(hours_ago: float, price: float = 0.5, vol: float = 10000.0) -> SnapshotPoint:
    return SnapshotPoint(
        timestamp=NOW - timedelta(hours=hours_ago),
        probability=price,
        volume_24h=vol,
    )


class TestGetWindow:
    def test_filters_points_older_than_lookback(self):
        points = [make_point(h) for h in (30, 25, 23, 12, 1)]
        window = get_window(points, hours_back=24, now=NOW)
        assert len(window) == 3  # 23h, 12h, 1h만 포함

    def test_returns_sorted_ascending(self):
        points = [make_point(1), make_point(23), make_point(12)]
        window = get_window(points, hours_back=24, now=NOW)
        timestamps = [p.timestamp for p in window]
        assert timestamps == sorted(timestamps)

    def test_excludes_future_points(self):
        points = [make_point(-1), make_point(2)]  # -1h = 미래
        window = get_window(points, hours_back=24, now=NOW)
        assert len(window) == 1

    def test_empty_input(self):
        assert get_window([], hours_back=24, now=NOW) == []


class TestIsWindowValid:
    def test_valid_window(self):
        # 5개 포인트, 22시간 커버 (>= 0.5 * 24h)
        window = get_window(
            [make_point(h) for h in (23, 18, 12, 6, 1)], 24, now=NOW
        )
        assert is_window_valid(window, hours_back=24) is True

    def test_too_few_points(self):
        # 4개 < min_points 5
        window = get_window(
            [make_point(h) for h in (23, 18, 12, 1)], 24, now=NOW
        )
        assert is_window_valid(window, hours_back=24) is False

    def test_insufficient_coverage(self):
        # 6개 포인트지만 전부 최근 3시간 -> 커버리지 3h < 12h (0.5 * 24h)
        # banana라면 "개수 충분"으로 통과했을 케이스
        window = get_window(
            [make_point(h) for h in (3.0, 2.5, 2.0, 1.5, 1.0, 0.5)], 24, now=NOW
        )
        assert is_window_valid(window, hours_back=24) is False

    def test_empty_window(self):
        assert is_window_valid([], hours_back=24) is False

    def test_custom_min_points(self):
        window = get_window(
            [make_point(h) for h in (23, 12, 1)], 24, now=NOW
        )
        assert is_window_valid(window, hours_back=24, min_points=3) is True
        assert is_window_valid(window, hours_back=24, min_points=4) is False


class TestMergeSnapshots:
    def test_dedupes_by_minute_with_primary_priority(self):
        db_point = SnapshotPoint(
            timestamp=datetime(2026, 7, 1, 10, 30, 15), probability=0.60
        )
        backfill_point = SnapshotPoint(
            timestamp=datetime(2026, 7, 1, 10, 30, 45), probability=0.99
        )
        merged = merge_snapshots([db_point], [backfill_point])
        assert len(merged) == 1
        assert merged[0].probability == 0.60  # DB(primary) 우선

    def test_merges_distinct_timestamps_sorted(self):
        db_points = [make_point(2), make_point(1)]
        backfill_points = [make_point(10), make_point(5)]
        merged = merge_snapshots(db_points, backfill_points)
        assert len(merged) == 4
        timestamps = [p.timestamp for p in merged]
        assert timestamps == sorted(timestamps)
