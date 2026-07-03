"""Night Watch 진입/청산 시그널 유닛테스트 (합성 스냅샷 fixture)."""
from datetime import datetime, timedelta, timezone

import pytest

from polybot.strategy.signals import (
    SnapshotPoint,
    NightWatchParams,
    ExitParams,
    evaluate_entry,
    evaluate_exit,
    compute_median_deviation,
    is_volume_spike,
    take_profit_target,
    merge_snapshots,
)

NOW = datetime(2026, 7, 4, 8, 0, 0)  # 토요일 UTC 08:00
PARAMS = NightWatchParams()


def make_snapshots(
    prices,
    volumes=None,
    now=NOW,
    span_hours=24.0,
):
    """합성 스냅샷 생성: span_hours 구간에 균등 분포, 오래된 것 먼저.

    Args:
        prices: YES 가격 리스트 (시간순)
        volumes: volume_24h 리스트 (없으면 일정한 10000)
        now: 기준 시각
        span_hours: 첫 스냅샷 ~ 마지막 스냅샷 간격
    """
    n = len(prices)
    if volumes is None:
        volumes = [10000.0] * n
    step = span_hours / (n - 1) if n > 1 else 0
    return [
        SnapshotPoint(
            timestamp=now - timedelta(hours=span_hours - i * step),
            probability=prices[i],
            volume_24h=volumes[i],
        )
        for i in range(n)
    ]


class TestEvaluateEntry:
    """진입 시그널 경계 케이스."""

    def test_case1_yes_drop_at_threshold_enters(self):
        """편차 -0.05 정확히 (경계) → YES 매수 진입."""
        # median = 0.60, 현재가 0.55 → dev = -0.05
        snaps = make_snapshots([0.60] * 12)
        signal = evaluate_entry(0.55, snaps, NOW, PARAMS)
        assert signal.should_enter is True
        assert signal.side_index == 0  # YES 매수 (복원 기대)
        assert signal.buy_price == pytest.approx(0.55)
        assert signal.deviation == pytest.approx(-0.05)
        assert signal.median == pytest.approx(0.60)

    def test_case2_deviation_below_min_rejected(self):
        """편차 -0.049 (경계 미만) → 진입 거부."""
        snaps = make_snapshots([0.60] * 12)
        signal = evaluate_entry(0.551, snaps, NOW, PARAMS)
        assert signal.should_enter is False
        assert signal.reason.startswith("dev_below_min")

    def test_case3_yes_rise_buys_no_token(self):
        """편차 +0.06 (상승 이탈) → NO 토큰 매수 (no_price 미전달 시 1-p 폴백)."""
        snaps = make_snapshots([0.60] * 12)
        signal = evaluate_entry(0.66, snaps, NOW, PARAMS)
        assert signal.should_enter is True
        assert signal.side_index == 1  # NO 매수
        assert signal.buy_price == pytest.approx(0.34)  # 폴백: 1 - 0.66
        assert signal.deviation == pytest.approx(0.06)

    def test_case3b_no_price_uses_actual_outcome_price(self):
        """NO 매수가는 1-p 근사가 아닌 실제 outcomePrices[1]을 사용."""
        # 얇은 호가: YES 0.66 + NO 0.32 = 0.98 (합이 1이 아님)
        snaps = make_snapshots([0.60] * 12)
        signal = evaluate_entry(0.66, snaps, NOW, PARAMS, current_no_price=0.32)
        assert signal.should_enter is True
        assert signal.side_index == 1
        assert signal.buy_price == pytest.approx(0.32)  # 0.34(1-p)가 아님

    def test_case3c_band_check_uses_actual_no_price(self):
        """1-p로는 밴드 안(0.34)이지만 실제 NO 가격이 밴드 밖(0.29)이면 거부."""
        snaps = make_snapshots([0.60] * 12)
        signal = evaluate_entry(0.66, snaps, NOW, PARAMS, current_no_price=0.29)
        assert signal.should_enter is False
        assert signal.reason.startswith("price_out_of_band")

    def test_case4_volume_spike_blocks_entry(self):
        """거래량 급증 (최근 3h >= 1.5x 윈도우 평균) → 진짜 뉴스로 보고 거부."""
        # 12개 스냅샷: 마지막 2개(최근 ~4.4h)가 30000, 나머지 10000
        # 윈도우 평균 ~13333, 최근 3h 평균 30000 = 2.25x > 1.5x
        volumes = [10000.0] * 10 + [30000.0, 30000.0]
        snaps = make_snapshots([0.60] * 12, volumes=volumes)
        signal = evaluate_entry(0.50, snaps, NOW, PARAMS)
        assert signal.should_enter is False
        assert signal.reason == "volume_spike_news"

    def test_case5_volume_just_below_spike_enters(self):
        """거래량이 급증 배수 미만이면 진입 허용."""
        # 균일 거래량 → 최근/전체 비율 1.0 < 1.5
        snaps = make_snapshots([0.60] * 12, volumes=[10000.0] * 12)
        signal = evaluate_entry(0.50, snaps, NOW, PARAMS)
        assert signal.should_enter is True

    def test_case6_buy_price_below_band_rejected(self):
        """매수 토큰 가격 < 0.30 → 거부 (붕괴 시장 배제)."""
        # median 0.36, 현재가 0.28 → dev = -0.08 통과하지만 가격 밴드 이탈
        snaps = make_snapshots([0.36] * 12)
        signal = evaluate_entry(0.28, snaps, NOW, PARAMS)
        assert signal.should_enter is False
        assert signal.reason.startswith("price_out_of_band")

    def test_case7_buy_price_above_band_rejected(self):
        """매수 토큰 가격 > 0.90 → 거부."""
        # median 0.85, 현재가 0.92 → dev = +0.07 → NO 매수가 = 0.08 < 0.30 거부
        # YES 하락 케이스로 상한 검증: median 0.98, 현재가 0.92 → dev = -0.06, YES 매수가 0.92 > 0.90
        snaps = make_snapshots([0.98] * 12)
        signal = evaluate_entry(0.92, snaps, NOW, PARAMS)
        assert signal.should_enter is False
        assert signal.reason.startswith("price_out_of_band")

    def test_case8_band_boundary_inclusive(self):
        """매수가 0.30/0.90 정확히 (경계 포함) → 진입 허용."""
        # 하한: median 0.36, 현재가 0.30
        snaps_low = make_snapshots([0.36] * 12)
        signal_low = evaluate_entry(0.30, snaps_low, NOW, PARAMS)
        assert signal_low.should_enter is True
        assert signal_low.buy_price == pytest.approx(0.30)

        # 상한: median 0.96, 현재가 0.90
        snaps_high = make_snapshots([0.96] * 12)
        signal_high = evaluate_entry(0.90, snaps_high, NOW, PARAMS)
        assert signal_high.should_enter is True
        assert signal_high.buy_price == pytest.approx(0.90)

    def test_case9_too_few_snapshots_rejected(self):
        """스냅샷 수 < min_points(5) → 윈도우 invalid → 거부."""
        snaps = make_snapshots([0.60] * 4)
        signal = evaluate_entry(0.50, snaps, NOW, PARAMS)
        assert signal.should_enter is False
        assert signal.reason.startswith("window_invalid")

    def test_case10_low_coverage_rejected(self):
        """스냅샷은 많아도 커버 시간 < 50% → 거부 (banana 버그 수정 검증)."""
        # 최근 6시간에만 12개 (24h의 25% 커버)
        snaps = make_snapshots([0.60] * 12, span_hours=6.0)
        signal = evaluate_entry(0.50, snaps, NOW, PARAMS)
        assert signal.should_enter is False
        assert signal.reason.startswith("window_invalid")

    def test_case11_empty_snapshots_rejected(self):
        """스냅샷 없음 (cold start, 백필 실패) → 거부."""
        signal = evaluate_entry(0.50, [], NOW, PARAMS)
        assert signal.should_enter is False
        assert signal.reason.startswith("window_invalid")

    def test_case12_median_uses_statistics_median(self):
        """median이 평균이 아닌 중앙값인지 검증 (outlier에 강건)."""
        # [0.50 x 11, 0.90 x 1] → median 0.50 (평균이면 ~0.533)
        prices = [0.50] * 11 + [0.90]
        snaps = make_snapshots(prices)
        median, dev = compute_median_deviation(0.44, snaps)
        assert median == pytest.approx(0.50)
        assert dev == pytest.approx(-0.06)

        signal = evaluate_entry(0.44, snaps, NOW, PARAMS)
        assert signal.should_enter is True
        assert signal.median == pytest.approx(0.50)


class TestVolumeSpike:
    def test_no_volume_data_is_not_spike(self):
        """volume 데이터 없음 (백필 전용) → 판정 불가 → False."""
        snaps = [
            SnapshotPoint(NOW - timedelta(hours=h), 0.6, None)
            for h in range(24, 0, -2)
        ]
        assert is_volume_spike(snaps, NOW) is False

    def test_spike_boundary_exact_multiplier(self):
        """최근 평균 = 정확히 1.5x → 급증으로 판정 (>= 경계)."""
        # 전체 평균 = (10*9000 + 2*15000)/12 = 10000, 최근 2개 평균 15000 = 정확히 1.5x
        volumes = [9000.0] * 10 + [15000.0, 15000.0]
        snaps = make_snapshots([0.6] * 12, volumes=volumes)
        assert is_volume_spike(snaps, NOW, recent_hours=3.0, spike_mult=1.5) is True


class TestEvaluateExit:
    """청산 시그널 (우선순위: SL → TP → max_holding → time_exit)."""

    def _exit(self, buy_price, current_price, holding_hours=1.0, hours_left=100.0):
        return evaluate_exit(
            buy_price=buy_price,
            current_price=current_price,
            buy_timestamp=NOW - timedelta(hours=holding_hours),
            market_end_date=NOW + timedelta(hours=hours_left),
            now=NOW,
            params=ExitParams(),
        )

    def test_stop_loss_at_boundary(self):
        """P&L -6% 정확히 → 손절."""
        should_sell, reason = self._exit(0.50, 0.47)
        assert should_sell is True
        assert reason == "stop_loss"

    def test_hold_just_above_stop_loss(self):
        """P&L -5.9% → 보유 유지."""
        should_sell, reason = self._exit(0.50, 0.4705)
        assert should_sell is False
        assert reason == "hold"

    def test_take_profit_at_boundary(self):
        """P&L +6% 정확히 → 익절."""
        should_sell, reason = self._exit(0.50, 0.53)
        assert should_sell is True
        assert reason == "take_profit"

    def test_take_profit_capped_at_099(self):
        """§3.5: 목표가 buy*(1.06)이 0.99 초과 시 0.99 캡."""
        # buy 0.95 → 무캡 목표 1.007 (도달 불가) → 캡 0.99
        assert take_profit_target(0.95, 0.06) == pytest.approx(0.99)
        should_sell, reason = self._exit(0.95, 0.99)
        assert should_sell is True
        assert reason == "take_profit"
        # 0.98은 캡 목표 미달 → 보유
        should_sell, _ = self._exit(0.95, 0.98)
        assert should_sell is False

    def test_max_holding_exit(self):
        """보유 24h 초과 → 복원 실패로 회전."""
        should_sell, reason = self._exit(0.50, 0.51, holding_hours=24.0)
        assert should_sell is True
        assert reason == "max_holding"

    def test_time_exit_near_resolution(self):
        """해결 12h 이내 → 청산."""
        should_sell, reason = self._exit(0.50, 0.51, hours_left=11.9)
        assert should_sell is True
        assert reason == "time_exit"

    def test_time_exit_converts_aware_end_date_to_utc(self):
        """비UTC aware endDate는 UTC 변환 후 판정 (offset 오차 방지)."""
        # 해결까지 11h (naive UTC 기준) → time_exit여야 함.
        # +02:00 표현으로 넘겨도 같은 순간이므로 결과가 같아야 한다.
        # (버그 시나리오: tzinfo만 떼면 13h로 계산되어 hold로 오판)
        plus2 = timezone(timedelta(hours=2))
        end_aware = (NOW + timedelta(hours=11)).replace(tzinfo=timezone.utc).astimezone(plus2)
        should_sell, reason = evaluate_exit(
            buy_price=0.50,
            current_price=0.51,
            buy_timestamp=NOW - timedelta(hours=1),
            market_end_date=end_aware,
            now=NOW,
            params=ExitParams(),
        )
        assert should_sell is True
        assert reason == "time_exit"

    def test_stop_loss_beats_max_holding(self):
        """우선순위: 손절이 max_holding보다 먼저."""
        should_sell, reason = self._exit(0.50, 0.40, holding_hours=30.0)
        assert should_sell is True
        assert reason == "stop_loss"


class TestMergeSnapshots:
    def test_dedupe_prefers_db_point(self):
        """같은 분(minute)의 중복은 DB 스냅샷 우선."""
        ts = datetime(2026, 7, 4, 7, 30, 15)
        db = [SnapshotPoint(ts, 0.60, 10000.0)]
        backfill = [
            SnapshotPoint(ts.replace(second=40), 0.99, None),  # 같은 분 → DB 우선
            SnapshotPoint(ts - timedelta(minutes=10), 0.58, None),
        ]
        merged = merge_snapshots(db, backfill)
        assert len(merged) == 2
        assert merged[0].probability == pytest.approx(0.58)
        assert merged[1].probability == pytest.approx(0.60)  # 0.99가 아님
        assert merged[1].volume_24h == pytest.approx(10000.0)

    def test_sorted_ascending(self):
        base = datetime(2026, 7, 4, 7, 0, 0)
        backfill = [
            SnapshotPoint(base + timedelta(minutes=20), 0.3),
            SnapshotPoint(base, 0.1),
            SnapshotPoint(base + timedelta(minutes=10), 0.2),
        ]
        merged = merge_snapshots([], backfill)
        assert [s.probability for s in merged] == [0.1, 0.2, 0.3]
