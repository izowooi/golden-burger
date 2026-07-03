"""시간 검증된 스냅샷 윈도우 테스트 (§3.2 banana 버그 수정)."""
from datetime import datetime, timedelta

from polybot.strategy.signals import Snap, get_window, is_window_valid, merge_snapshots

NOW = datetime(2026, 7, 3, 12, 0, 0)


def snaps_at_minutes_ago(minutes_list, price=0.92):
    """NOW 기준 N분 전 스냅샷 리스트 생성."""
    return [Snap(NOW - timedelta(minutes=m), price) for m in minutes_list]


class TestGetWindow:
    def test_filters_out_old_snapshots(self):
        snaps = snaps_at_minutes_ago([500, 400, 300, 100, 50, 10])  # 6h = 360분
        window = get_window(snaps, hours_back=6, now=NOW)
        assert len(window) == 4  # 500, 400분 전은 제외

    def test_boundary_inclusive(self):
        snaps = snaps_at_minutes_ago([360, 359, 361])
        window = get_window(snaps, hours_back=6, now=NOW)
        # ts >= now - 6h: 360분 전(경계 포함), 359분 전 포함 / 361분 전 제외
        assert len(window) == 2

    def test_returns_sorted_ascending(self):
        snaps = snaps_at_minutes_ago([10, 300, 100, 200])
        window = get_window(snaps, hours_back=6, now=NOW)
        timestamps = [s.timestamp for s in window]
        assert timestamps == sorted(timestamps)

    def test_empty_input(self):
        assert get_window([], hours_back=6, now=NOW) == []


class TestIsWindowValid:
    def test_valid_window(self):
        # 6h 윈도우: 5개 포인트, 커버리지 5h (>= 3h = 0.5 * 6h)
        window = get_window(
            snaps_at_minutes_ago([300, 240, 180, 60, 0]), hours_back=6, now=NOW
        )
        assert is_window_valid(window, hours_back=6) is True

    def test_too_few_points(self):
        # 커버리지는 충분하지만 포인트 4개 < min_points 5
        window = get_window(
            snaps_at_minutes_ago([300, 200, 100, 0]), hours_back=6, now=NOW
        )
        assert is_window_valid(window, hours_back=6) is False

    def test_insufficient_coverage(self):
        # 포인트 6개지만 최근 1h에 몰림 (커버리지 50분 < 3h)
        window = get_window(
            snaps_at_minutes_ago([50, 40, 30, 20, 10, 0]), hours_back=6, now=NOW
        )
        assert is_window_valid(window, hours_back=6) is False

    def test_coverage_boundary(self):
        # 커버리지 정확히 3h (= 0.5 * 6h) → 유효 (>= 조건)
        window = get_window(
            snaps_at_minutes_ago([180, 135, 90, 45, 0]), hours_back=6, now=NOW
        )
        assert is_window_valid(window, hours_back=6) is True

    def test_empty_window(self):
        assert is_window_valid([], hours_back=6) is False

    def test_custom_min_points(self):
        window = get_window(
            snaps_at_minutes_ago([300, 150, 0]), hours_back=6, now=NOW
        )
        assert is_window_valid(window, hours_back=6, min_points=3) is True
        assert is_window_valid(window, hours_back=6, min_points=4) is False


class TestMergeSnapshots:
    def test_merges_and_sorts(self):
        db = snaps_at_minutes_ago([300, 100], price=0.92)
        backfill = snaps_at_minutes_ago([200, 50], price=0.91)
        merged = merge_snapshots(db, backfill)
        assert len(merged) == 4
        timestamps = [s.timestamp for s in merged]
        assert timestamps == sorted(timestamps)

    def test_dedupes_same_minute_primary_wins(self):
        db = snaps_at_minutes_ago([100], price=0.92)
        backfill = snaps_at_minutes_ago([100], price=0.70)  # 같은 시각
        merged = merge_snapshots(db, backfill)
        assert len(merged) == 1
        assert merged[0].probability == 0.92  # DB(primary) 우선

    def test_empty_inputs(self):
        assert merge_snapshots([], []) == []
        db = snaps_at_minutes_ago([100])
        assert merge_snapshots(db, []) == db
        assert merge_snapshots([], db) == db
