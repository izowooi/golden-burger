"""시간 검증된 스냅샷 윈도우 로직 테스트 (§3.2 banana 버그 수정 검증)."""
from datetime import datetime, timedelta

from polybot.strategy.signals import (
    PricePoint,
    get_window,
    is_window_valid,
    merge_price_points,
)

NOW = datetime(2026, 7, 3, 12, 0, 0)


def points_at_hours_ago(hours_ago_list, prob=0.10, volume=None):
    """N시간 전 스냅샷들 생성 (오래된 것 먼저)."""
    return [
        PricePoint(
            timestamp=NOW - timedelta(hours=h), probability=prob, volume_24h=volume
        )
        for h in sorted(hours_ago_list, reverse=True)
    ]


class TestGetWindow:
    def test_filters_points_older_than_window(self):
        snapshots = points_at_hours_ago([30, 25, 20, 10, 5, 1])
        window = get_window(snapshots, hours_back=24, now=NOW)
        assert len(window) == 4  # 30h, 25h 전 스냅샷 제외

    def test_includes_boundary_point(self):
        snapshots = points_at_hours_ago([24, 12, 1])
        window = get_window(snapshots, hours_back=24, now=NOW)
        # 정확히 24h 전 포인트는 포함 (>= cutoff)
        assert len(window) == 3

    def test_returns_sorted_ascending(self):
        # 입력 순서가 뒤섞여도 오름차순으로 반환
        snapshots = points_at_hours_ago([1, 20, 10, 5])
        shuffled = [snapshots[2], snapshots[0], snapshots[3], snapshots[1]]
        window = get_window(shuffled, hours_back=24, now=NOW)
        timestamps = [p.timestamp for p in window]
        assert timestamps == sorted(timestamps)

    def test_empty_snapshots(self):
        assert get_window([], hours_back=24, now=NOW) == []

    def test_seven_day_window(self):
        # 전략 기본 lookback(7d=168h)에서도 timestamp 기반으로 정확히 자른다
        snapshots = points_at_hours_ago([200, 168, 100, 50, 1])
        window = get_window(snapshots, hours_back=168, now=NOW)
        assert len(window) == 4  # 200h 전만 제외


class TestIsWindowValid:
    def test_valid_with_enough_points_and_coverage(self):
        # 6개 포인트, 140h 커버리지 (>= 0.5 * 168h = 84h)
        window = points_at_hours_ago([140, 100, 70, 40, 10, 1])
        assert is_window_valid(window, hours_back=168) is True

    def test_invalid_too_few_points(self):
        # 커버리지는 충분하지만 포인트 4개 < min_points 5
        window = points_at_hours_ago([140, 90, 40, 1])
        assert is_window_valid(window, hours_back=168) is False

    def test_invalid_low_coverage(self):
        # banana 버그 시나리오: 포인트는 많지만 전부 최근 3시간에 몰림
        # (Jenkins 재시작 직후) → 7d 윈도우로 판정하면 안 된다
        window = points_at_hours_ago([3.0, 2.5, 2.0, 1.5, 1.0, 0.5, 0.0])
        assert is_window_valid(window, hours_back=168) is False

    def test_valid_at_exact_coverage_boundary(self):
        # 커버리지 정확히 84h = 0.5 * 168h → valid (>=)
        window = points_at_hours_ago([84, 60, 40, 20, 0])
        assert is_window_valid(window, hours_back=168) is True

    def test_invalid_empty_window(self):
        assert is_window_valid([], hours_back=168) is False

    def test_custom_min_points(self):
        window = points_at_hours_ago([140, 70, 1])
        assert is_window_valid(window, hours_back=168, min_points=3) is True
        assert is_window_valid(window, hours_back=168, min_points=4) is False


class TestMergePricePoints:
    def test_merges_and_sorts(self):
        db_points = points_at_hours_ago([1, 3], prob=0.10)
        backfill = points_at_hours_ago([2, 4], prob=0.09)
        merged = merge_price_points(db_points, backfill)
        assert len(merged) == 4
        timestamps = [p.timestamp for p in merged]
        assert timestamps == sorted(timestamps)

    def test_db_wins_on_duplicate_minute(self):
        ts = NOW - timedelta(hours=1)
        db_point = PricePoint(timestamp=ts, probability=0.10, volume_24h=500.0)
        backfill_point = PricePoint(
            timestamp=ts.replace(second=30), probability=0.99
        )
        merged = merge_price_points([db_point], [backfill_point])
        # 같은 분(minute)이면 중복 제거, DB 스냅샷 우선
        assert len(merged) == 1
        assert merged[0].probability == 0.10

    def test_db_volume_preserved(self):
        # 거래량 게이트가 병합 후에도 동작하도록 DB 스냅샷의 volume 유지
        db_points = points_at_hours_ago([1], volume=1234.0)
        backfill = points_at_hours_ago([2])  # 백필은 volume 없음
        merged = merge_price_points(db_points, backfill)
        volumes = {p.volume_24h for p in merged}
        assert 1234.0 in volumes

    def test_empty_backfill(self):
        db_points = points_at_hours_ago([1, 2])
        merged = merge_price_points(db_points, [])
        assert len(merged) == 2
