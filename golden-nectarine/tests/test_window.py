"""시간 윈도우 검증 로직 테스트 (§3.2 timestamp 기반 윈도우).

banana의 '개수 기반 윈도우' 버그(Jenkins 중단 시 15분 윈도우가 몇 시간을
커버)를 수정한 get_window / is_window_valid를 검증한다.
Bottom Fisher는 20일(480h) 룩백이라 이 검증이 특히 중요하다.
"""
from datetime import datetime, timedelta

from polybot.strategy.signals import PricePoint, get_window, is_window_valid

NOW = datetime(2026, 7, 3, 12, 0, 0)


def point(hours_ago: float, price: float = 0.3) -> PricePoint:
    return PricePoint(NOW - timedelta(hours=hours_ago), price)


def test_get_window_filters_by_timestamp():
    """hours_back 밖의 오래된 포인트는 제외된다."""
    points = [point(600), point(500), point(400), point(100), point(1)]
    window = get_window(points, hours_back=480.0, now=NOW)
    assert len(window) == 3  # 400/100/1h 전만 포함 (480h = 20일)


def test_get_window_includes_boundary():
    """정확히 hours_back 전 포인트는 포함된다 (>=)."""
    points = [point(480), point(1)]
    window = get_window(points, hours_back=480.0, now=NOW)
    assert len(window) == 2


def test_get_window_sorts_unordered_input():
    points = [point(1, 0.3), point(400, 0.1), point(100, 0.2)]
    window = get_window(points, hours_back=480.0, now=NOW)
    assert [p.price for p in window] == [0.1, 0.2, 0.3]


def test_get_window_empty_input():
    assert get_window([], hours_back=480.0, now=NOW) == []


def test_window_valid_with_enough_points_and_coverage():
    """포인트 20개 + 커버리지 456h/480h(95%) → 유효."""
    window = get_window(
        [point(h) for h in range(456, -1, -24)], 480.0, NOW
    )
    assert is_window_valid(window, hours_back=480.0) is True


def test_window_invalid_with_too_few_points():
    """커버리지가 충분해도 포인트 19개 < 20개 → 무효."""
    hours = [456] + list(range(408, -1, -24))
    window = get_window([point(h) for h in hours], 480.0, NOW)
    assert is_window_valid(window, hours_back=480.0) is False


def test_window_invalid_with_poor_coverage():
    """포인트 25개지만 최근 48h뿐 → 커버리지 2일 < 19일(95%) → 무효.

    백필 실패 + 스냅샷 이틀치만 쌓인 콜드스타트 상황 - 이때 진입하면 안 된다.
    """
    window = get_window([point(h) for h in range(48, -1, -2)], 480.0, NOW)
    assert is_window_valid(window, hours_back=480.0) is False


def test_window_valid_at_exact_coverage_boundary():
    """커버리지가 정확히 min_coverage * hours_back이면 유효 (>=)."""
    # span = 456h = 0.95 * 480h, 포인트도 20개
    window = get_window(
        [point(h) for h in range(456, -1, -24)], 480.0, NOW
    )
    assert is_window_valid(window, hours_back=480.0) is True


def test_window_custom_min_points():
    window = get_window([point(h) for h in (456, 228, 0)], 480.0, NOW)
    assert is_window_valid(window, hours_back=480.0, min_points=3) is True
    assert is_window_valid(window, hours_back=480.0, min_points=4) is False
