"""시간 윈도우 검증 + 한산 시간대(quiet hours) 파싱/판정 유닛테스트."""
from datetime import datetime, timedelta

import pytest

from polybot.strategy.signals import (
    SnapshotPoint,
    get_window,
    is_window_valid,
    parse_quiet_hours,
    is_quiet_hour,
    is_weekend,
    is_quiet_time,
)

NOW = datetime(2026, 7, 1, 12, 0, 0)  # 수요일 UTC 12:00


def points_at_hours_ago(hours_list, now=NOW):
    """N시간 전 스냅샷들 생성 (오래된 것 먼저)."""
    return [
        SnapshotPoint(timestamp=now - timedelta(hours=h), probability=0.5)
        for h in sorted(hours_list, reverse=True)
    ]


class TestGetWindow:
    def test_filters_by_timestamp(self):
        """hours_back보다 오래된 스냅샷은 제외."""
        snaps = points_at_hours_ago([30, 25, 20, 10, 1])
        window = get_window(snaps, 24, NOW)
        assert len(window) == 3  # 20h, 10h, 1h

    def test_boundary_inclusive(self):
        """정확히 hours_back 전 스냅샷은 포함 (ts >= cutoff)."""
        snaps = points_at_hours_ago([24, 12])
        window = get_window(snaps, 24, NOW)
        assert len(window) == 2

    def test_sorted_oldest_first(self):
        snaps = points_at_hours_ago([1, 10, 5])
        window = get_window(snaps, 24, NOW)
        timestamps = [s.timestamp for s in window]
        assert timestamps == sorted(timestamps)

    def test_empty_input(self):
        assert get_window([], 24, NOW) == []


class TestIsWindowValid:
    def test_valid_window(self):
        """포인트 5개 + 커버리지 >= 50% → 유효."""
        window = get_window(points_at_hours_ago([20, 15, 10, 5, 1]), 24, NOW)
        assert is_window_valid(window, 24) is True

    def test_too_few_points(self):
        """포인트 4개 < min_points(5) → 무효."""
        window = get_window(points_at_hours_ago([20, 15, 10, 5]), 24, NOW)
        assert is_window_valid(window, 24) is False

    def test_low_coverage_invalid(self):
        """포인트는 많아도 커버 11.9h < 12h(50% of 24h) → 무효 (banana 버그)."""
        hours = [11.9, 10, 8, 6, 4, 2, 0.5]
        window = get_window(points_at_hours_ago(hours), 24, NOW)
        assert is_window_valid(window, 24) is False

    def test_coverage_boundary_exact_half(self):
        """커버리지 정확히 50% → 유효 (>= 경계)."""
        hours = [12, 9, 6, 3, 0]
        window = get_window(points_at_hours_ago(hours), 24, NOW)
        assert is_window_valid(window, 24) is True

    def test_empty_window(self):
        assert is_window_valid([], 24) is False


class TestParseQuietHours:
    def test_normal_range(self):
        assert parse_quiet_hours("6-13") == (6, 13)

    def test_overnight_range(self):
        """자정을 넘는 "22-4" 형식 지원."""
        assert parse_quiet_hours("22-4") == (22, 4)

    def test_whitespace_tolerated(self):
        assert parse_quiet_hours(" 6 - 13 ") == (6, 13)

    @pytest.mark.parametrize("bad", [
        "6",           # 구분자 없음
        "6-13-20",     # 구간 3개
        "a-b",         # 정수 아님
        "6-24",        # 범위 초과
        "-1-13",       # 음수 (파싱 시 구간 3개로 해석되어 거부)
        "6-6",         # 빈 구간
        "",            # 빈 문자열
    ])
    def test_invalid_specs_raise(self, bad):
        with pytest.raises(ValueError):
            parse_quiet_hours(bad)


class TestIsQuietHour:
    def test_normal_range_boundaries(self):
        """[start, end): 6시 포함, 13시 미포함."""
        r = (6, 13)
        assert is_quiet_hour(datetime(2026, 7, 1, 6, 0), r) is True
        assert is_quiet_hour(datetime(2026, 7, 1, 12, 59), r) is True
        assert is_quiet_hour(datetime(2026, 7, 1, 13, 0), r) is False
        assert is_quiet_hour(datetime(2026, 7, 1, 5, 59), r) is False

    def test_overnight_range(self):
        """22-4: 22,23,0,1,2,3시 quiet / 4시~21시 아님."""
        r = (22, 4)
        assert is_quiet_hour(datetime(2026, 7, 1, 22, 0), r) is True
        assert is_quiet_hour(datetime(2026, 7, 1, 23, 30), r) is True
        assert is_quiet_hour(datetime(2026, 7, 1, 0, 0), r) is True
        assert is_quiet_hour(datetime(2026, 7, 1, 3, 59), r) is True
        assert is_quiet_hour(datetime(2026, 7, 1, 4, 0), r) is False
        assert is_quiet_hour(datetime(2026, 7, 1, 12, 0), r) is False
        assert is_quiet_hour(datetime(2026, 7, 1, 21, 59), r) is False


class TestWeekend:
    def test_saturday_and_sunday(self):
        assert is_weekend(datetime(2026, 7, 4, 15, 0)) is True   # 토
        assert is_weekend(datetime(2026, 7, 5, 15, 0)) is True   # 일
        assert is_weekend(datetime(2026, 7, 3, 15, 0)) is False  # 금
        assert is_weekend(datetime(2026, 7, 6, 15, 0)) is False  # 월

    def test_weekend_overrides_hours_when_enabled(self):
        """주말 + weekends_quiet=True → quiet hours 밖이어도 진입 허용."""
        saturday_evening = datetime(2026, 7, 4, 20, 0)  # 토 20:00 (6-13 밖)
        assert is_quiet_time(saturday_evening, (6, 13), weekends_quiet=True) is True

    def test_weekend_not_quiet_when_disabled(self):
        """weekends_quiet=False → 주말에도 시각 기준으로만 판정."""
        saturday_evening = datetime(2026, 7, 4, 20, 0)
        assert is_quiet_time(saturday_evening, (6, 13), weekends_quiet=False) is False

        saturday_morning = datetime(2026, 7, 4, 8, 0)
        assert is_quiet_time(saturday_morning, (6, 13), weekends_quiet=False) is True

    def test_weekday_uses_hours(self):
        """평일은 항상 시각 기준."""
        wednesday_morning = datetime(2026, 7, 1, 8, 0)
        wednesday_night = datetime(2026, 7, 1, 20, 0)
        assert is_quiet_time(wednesday_morning, (6, 13), weekends_quiet=True) is True
        assert is_quiet_time(wednesday_night, (6, 13), weekends_quiet=True) is False
