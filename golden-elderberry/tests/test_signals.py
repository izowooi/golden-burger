"""Panic Fade 진입/청산 시그널 유닛테스트 (합성 스냅샷 fixture)."""
from datetime import datetime, timedelta

import pytest

from polybot.strategy.signals import (
    PricePoint,
    PanicFadeParams,
    evaluate_panic_fade,
    evaluate_exit,
    merge_price_series,
)

NOW = datetime(2026, 7, 1, 12, 0, 0)


def make_series(points):
    """(hours_ago, yes_price) 리스트 -> PricePoint 리스트 (합성 스냅샷)."""
    return [
        PricePoint(NOW - timedelta(hours=h), p)
        for h, p in points
    ]


def panic_fade_series(
    peak=0.85,
    bottom=0.60,
    yes_side=True,
    stab_noise=0.0,
    still_falling=False,
):
    """전형적인 panic fade 시계열 생성.

    - 48h~6h 전: peak 근처에서 안정 (favorite)
    - 3h~1h 전: 급락
    - 최근 45분: bottom 근처에서 안정화 (>=3 스냅샷)

    yes_side=False면 NO favorite 시장 (YES 가격 = 1 - p 로 저장).
    """
    raw = []
    # 급락 전 구간 (ref 윈도우): 47h ~ 4h 전, 약 2시간 간격
    h = 47.0
    while h >= 4.0:
        raw.append((h, peak))
        h -= 2.0
    # 급락 구간
    raw.append((2.5, peak - 0.05))
    raw.append((2.0, peak - 0.12))
    raw.append((1.5, bottom + 0.03))
    # 바닥 안정화 구간 (최근 45분 내 3개 이상)
    if still_falling:
        raw.append((0.6, bottom + 0.04))
        raw.append((0.3, bottom + 0.02))
        raw.append((0.1, bottom + 0.01))  # 현재가는 이보다 더 낮게 줄 것
    else:
        raw.append((0.6, bottom + stab_noise))
        raw.append((0.3, bottom))
        raw.append((0.1, bottom))

    if yes_side:
        return make_series(raw)
    # NO favorite: YES 가격으로 뒤집어 저장
    return make_series([(h, round(1.0 - p, 6)) for h, p in raw])


DEFAULT_PARAMS = PanicFadeParams()


class TestPanicFadeEntry:
    """진입 시그널 O/X 경계 케이스."""

    def test_entry_yes_side(self):
        """케이스 1: 전형적 panic fade (YES favorite) -> 진입 O."""
        series = panic_fade_series(peak=0.85, bottom=0.60)
        signal = evaluate_panic_fade(series, 0.60, DEFAULT_PARAMS, NOW)
        assert signal.entry is True
        assert signal.side == "Yes"
        assert signal.token_index == 0
        assert signal.ref_price == pytest.approx(0.85)
        assert signal.drop == pytest.approx(0.25)

    def test_entry_no_side_converted(self):
        """케이스 2: NO favorite 시장 -> 1-p 환산으로 진입 O, side=No."""
        series = panic_fade_series(peak=0.85, bottom=0.60, yes_side=False)
        # 현재 YES 가격 = 1 - 0.60 = 0.40
        signal = evaluate_panic_fade(series, 0.40, DEFAULT_PARAMS, NOW)
        assert signal.entry is True
        assert signal.side == "No"
        assert signal.token_index == 1
        assert signal.ref_price == pytest.approx(0.85)
        assert signal.current_price == pytest.approx(0.60)

    def test_no_entry_ref_below_min(self):
        """케이스 3: ref 0.65 < 0.70 (원래 favorite 아님) -> 진입 X."""
        series = panic_fade_series(peak=0.65, bottom=0.50)
        signal = evaluate_panic_fade(series, 0.50, DEFAULT_PARAMS, NOW)
        assert signal.entry is False
        assert signal.reason.startswith("ref_below_min")

    def test_no_entry_drop_too_small(self):
        """케이스 4: 낙폭 0.08 < 0.12 -> 진입 X."""
        series = panic_fade_series(peak=0.80, bottom=0.72)
        signal = evaluate_panic_fade(series, 0.72, DEFAULT_PARAMS, NOW)
        assert signal.entry is False
        assert signal.reason.startswith("drop_too_small")

    def test_entry_drop_exact_boundary(self):
        """케이스 5: 낙폭 정확히 0.12 (>= 경계) -> 진입 O."""
        series = panic_fade_series(peak=0.82, bottom=0.70)
        signal = evaluate_panic_fade(series, 0.70, DEFAULT_PARAMS, NOW)
        assert signal.entry is True
        assert signal.drop == pytest.approx(0.12)

    def test_no_entry_collapse_below_band(self):
        """케이스 6: 현재가 0.30 < 0.35 (완전 붕괴) -> 진입 X."""
        series = panic_fade_series(peak=0.85, bottom=0.30)
        signal = evaluate_panic_fade(series, 0.30, DEFAULT_PARAMS, NOW)
        assert signal.entry is False
        assert signal.reason.startswith("price_out_of_band")

    def test_no_entry_above_band(self):
        """케이스 7: 현재가 0.78 > 0.75 (낙폭 부족한 고가) -> 진입 X."""
        series = panic_fade_series(peak=0.95, bottom=0.78)
        signal = evaluate_panic_fade(series, 0.78, DEFAULT_PARAMS, NOW)
        assert signal.entry is False
        assert signal.reason.startswith("price_out_of_band")

    def test_no_entry_still_falling(self):
        """케이스 8: 현재가가 최근 45분 min보다 낮음 (하락 진행 중) -> 진입 X."""
        series = panic_fade_series(peak=0.85, bottom=0.60, still_falling=True)
        # 최근 45분 min = 0.61, 현재가 0.58 < min
        signal = evaluate_panic_fade(series, 0.58, DEFAULT_PARAMS, NOW)
        assert signal.entry is False
        assert signal.reason == "still_falling"

    def test_no_entry_not_stabilized_high_std(self):
        """케이스 9: 안정화 구간 std > 0.02 (출렁임) -> 진입 X."""
        series = panic_fade_series(peak=0.85, bottom=0.60, stab_noise=0.06)
        signal = evaluate_panic_fade(series, 0.60, DEFAULT_PARAMS, NOW)
        assert signal.entry is False
        assert signal.reason.startswith("not_stabilized_std")

    def test_no_entry_window_too_few_points(self):
        """케이스 10: 스냅샷 5개 미만 -> window_invalid -> 진입 X."""
        series = make_series([(40.0, 0.85), (20.0, 0.85), (0.2, 0.60)])
        signal = evaluate_panic_fade(series, 0.60, DEFAULT_PARAMS, NOW)
        assert signal.entry is False
        assert signal.reason == "window_invalid"

    def test_no_entry_window_low_coverage(self):
        """케이스 11: 포인트는 많지만 커버리지 < 48h*0.5 -> 진입 X.

        banana의 개수 기반 윈도우 버그 수정 검증 - 최근 몇 시간에
        몰린 스냅샷만으로는 48h 윈도우 신호를 계산하지 않는다.
        """
        series = make_series([
            (5.0, 0.85), (4.0, 0.85), (3.0, 0.85), (2.0, 0.73),
            (0.6, 0.60), (0.3, 0.60), (0.1, 0.60),
        ])
        signal = evaluate_panic_fade(series, 0.60, DEFAULT_PARAMS, NOW)
        assert signal.entry is False
        assert signal.reason == "window_invalid"

    def test_no_entry_stab_insufficient_snapshots(self):
        """케이스 12: 최근 45분 스냅샷 3개 미만 -> 진입 X."""
        raw = [(h, 0.85) for h in range(47, 3, -2)]
        raw += [(2.0, 0.73), (1.5, 0.62), (0.2, 0.60)]  # 45분 내 1개뿐
        series = make_series(raw)
        signal = evaluate_panic_fade(series, 0.60, DEFAULT_PARAMS, NOW)
        assert signal.entry is False
        assert signal.reason == "stab_insufficient_data"

    def test_no_entry_new_low_matching_own_snapshot(self):
        """케이스 14: 현재가가 방금 저장된 자기 스냅샷(신저가)과 같아도 하락 진행이면 진입 X.

        운영에서는 Phase 0이 현재가를 스냅샷으로 먼저 저장하므로 현재가는
        항상 stab 윈도우에 포함된다. min을 현재 포인트 포함으로 계산하면
        신저가 게이트가 절대 발동하지 않는다 - 직전 포인트들 기준으로 판정.
        """
        raw = [(h, 0.85) for h in range(47, 3, -2)]
        raw += [(2.5, 0.78), (2.0, 0.70), (1.5, 0.66)]
        # 매 사이클 신저가를 만드는 완만한 추가 하락 (std 0.012 <= 0.02)
        raw += [(0.6, 0.63), (0.3, 0.615), (0.05, 0.60)]
        series = make_series(raw)
        signal = evaluate_panic_fade(series, 0.60, DEFAULT_PARAMS, NOW)
        assert signal.entry is False
        assert signal.reason == "still_falling"

    def test_entry_current_equals_prior_bottom(self):
        """케이스 15: 현재가가 직전 스냅샷들의 min과 같으면(신저가 아님) 통과."""
        raw = [(h, 0.85) for h in range(47, 3, -2)]
        raw += [(2.5, 0.78), (2.0, 0.70), (1.5, 0.62)]
        raw += [(0.6, 0.61), (0.3, 0.60), (0.05, 0.60)]  # 바닥 유지
        series = make_series(raw)
        signal = evaluate_panic_fade(series, 0.60, DEFAULT_PARAMS, NOW)
        assert signal.entry is True

    def test_ref_excludes_recent_hours(self):
        """케이스 13: 급락 자체(최근 3h)는 ref 계산에서 제외된다.

        최근 3h에 일시 스파이크(0.95)가 있어도 ref는 3h 이전 최고가(0.85).
        """
        raw = [(h, 0.85) for h in range(47, 3, -2)]
        raw += [(2.5, 0.95), (2.0, 0.73)]  # 3h 이내 스파이크는 ref에 미포함
        raw += [(0.6, 0.60), (0.3, 0.60), (0.1, 0.60)]
        series = make_series(raw)
        signal = evaluate_panic_fade(series, 0.60, DEFAULT_PARAMS, NOW)
        assert signal.entry is True
        assert signal.ref_price == pytest.approx(0.85)


class TestExitSignal:
    """청산 시그널 (SL -> TP -> max_holding -> time_exit)."""

    def test_stop_loss(self):
        should, reason = evaluate_exit(
            buy_price=0.60, current_price=0.53,
            take_profit_percent=0.10, stop_loss_percent=-0.10,
            holding_hours=1.0, max_holding_hours=48.0,
            hours_left=100.0, exit_hours=24.0,
        )
        assert should is True
        assert reason == "stop_loss"

    def test_take_profit(self):
        should, reason = evaluate_exit(
            buy_price=0.60, current_price=0.67,
            take_profit_percent=0.10, stop_loss_percent=-0.10,
            holding_hours=1.0, max_holding_hours=48.0,
            hours_left=100.0, exit_hours=24.0,
        )
        assert should is True
        assert reason == "take_profit"

    def test_take_profit_099_cap(self):
        """진입가 0.95면 +10% 목표가(1.045)는 도달 불가 -> 0.99 캡에서 익절."""
        should, reason = evaluate_exit(
            buy_price=0.95, current_price=0.99,
            take_profit_percent=0.10, stop_loss_percent=-0.10,
            holding_hours=1.0, max_holding_hours=48.0,
            hours_left=100.0, exit_hours=24.0,
        )
        assert should is True
        assert reason == "take_profit"

    def test_max_holding_exceeded(self):
        """보유 48h 초과 (반등 실패) -> 청산."""
        should, reason = evaluate_exit(
            buy_price=0.60, current_price=0.61,
            take_profit_percent=0.10, stop_loss_percent=-0.10,
            holding_hours=48.5, max_holding_hours=48.0,
            hours_left=100.0, exit_hours=24.0,
        )
        assert should is True
        assert reason == "max_holding"

    def test_time_exit(self):
        """해결 24h 이내 -> 청산."""
        should, reason = evaluate_exit(
            buy_price=0.60, current_price=0.61,
            take_profit_percent=0.10, stop_loss_percent=-0.10,
            holding_hours=10.0, max_holding_hours=48.0,
            hours_left=20.0, exit_hours=24.0,
        )
        assert should is True
        assert reason == "time_exit"

    def test_hold(self):
        should, reason = evaluate_exit(
            buy_price=0.60, current_price=0.62,
            take_profit_percent=0.10, stop_loss_percent=-0.10,
            holding_hours=10.0, max_holding_hours=48.0,
            hours_left=100.0, exit_hours=24.0,
        )
        assert should is False
        assert reason == "hold"

    def test_priority_stop_loss_over_time_exit(self):
        """SL과 time_exit이 동시 충족이면 SL이 우선."""
        should, reason = evaluate_exit(
            buy_price=0.60, current_price=0.50,
            take_profit_percent=0.10, stop_loss_percent=-0.10,
            holding_hours=50.0, max_holding_hours=48.0,
            hours_left=10.0, exit_hours=24.0,
        )
        assert should is True
        assert reason == "stop_loss"


class TestMergePriceSeries:
    """DB 스냅샷 + 백필 병합."""

    def test_db_wins_on_duplicate_minute(self):
        db = [PricePoint(NOW - timedelta(minutes=10), 0.60)]
        backfill = [
            # 같은 분(11:50)의 30초 지점 - DB 스냅샷이 우선해야 함
            PricePoint((NOW - timedelta(minutes=10)).replace(second=30), 0.99),
            PricePoint(NOW - timedelta(minutes=20), 0.55),
        ]
        merged = merge_price_series(db, backfill)
        assert len(merged) == 2
        assert merged[0].price == 0.55
        assert merged[1].price == 0.60  # DB가 백필을 덮음

    def test_sorted_output(self):
        db = [PricePoint(NOW - timedelta(minutes=5), 0.61)]
        backfill = [
            PricePoint(NOW - timedelta(minutes=60), 0.70),
            PricePoint(NOW - timedelta(minutes=30), 0.65),
        ]
        merged = merge_price_series(db, backfill)
        assert [p.price for p in merged] == [0.70, 0.65, 0.61]
