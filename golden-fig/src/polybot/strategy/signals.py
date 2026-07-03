"""Hope Crusher 전략 시그널 - 순수 함수 모음.

이 모듈의 함수는 전부 순수 함수다: 스냅샷 리스트/숫자 입력 → 판정 출력.
API, DB, 설정 파일에 의존하지 않으므로 유닛테스트가 그대로 전략 검증이 된다.
scanner/trader는 여기서 판정만 받아 실행한다. 전략 변경 = 이 파일 수정.

전략 요약 (favorite-longshot bias의 미러):
- "D일까지 X가 일어날까" 롱샷 시장에서 YES 5~25% 구간이면 NO 토큰(75~95%)을 매수.
- 시간이 소진되어도 희망 보유자들이 앵커링 때문에 YES를 놓지 않아
  theta(시간 가치 소멸)가 늦게 반영된다 → NO를 사서 시간의 흐름을 수확.
- 단, "뭔가 실제로 일어나고 있는" 시장(YES 상승/급등)은 배제한다.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence

# 익절 목표가 상한: buy_price*(1+tp)가 0.99를 넘으면 0.99 도달 시 익절
TP_PRICE_CAP = 0.99

# 윈도우 유효성 기본값 (§3.2 timestamp 기반 윈도우)
WINDOW_MIN_POINTS = 5
WINDOW_MIN_COVERAGE = 0.5

# float 뺄셈 오차 보정 (예: 0.15 - 0.10 = 0.04999...가 0.05 경계를 통과하는 문제)
_EPS = 1e-9


@dataclass(frozen=True)
class PricePoint:
    """스냅샷 1개: YES 가격 기준. DB MarketSnapshot과 attribute 호환."""
    timestamp: datetime  # naive UTC
    probability: float   # YES 가격 (0.0~1.0)


@dataclass(frozen=True)
class SignalParams:
    """진입 판정 파라미터 (config에서 주입)."""
    yes_min: float = 0.05
    yes_max: float = 0.25
    yes_rise_block_24h: float = 0.02
    yes_spike_block_6h: float = 0.05
    entry_hours_min: float = 24.0
    entry_hours_max: float = 240.0
    min_liquidity: float = 10000.0
    min_volume_24h: float = 0.0
    rise_lookback_hours: float = 24.0
    spike_lookback_hours: float = 6.0


@dataclass(frozen=True)
class EntrySignal:
    """진입 판정 결과."""
    entry: bool
    reason: str


@dataclass(frozen=True)
class ExitSignal:
    """청산 판정 결과."""
    should_sell: bool
    reason: str  # "stop_loss" | "take_profit" | "time_exit" | "hold"


def get_window(
    snapshots: Sequence,
    hours_back: float,
    now: Optional[datetime] = None,
) -> List:
    """timestamp 기준으로 최근 hours_back 시간 내 스냅샷만 필터.

    banana의 "개수 기반 윈도우" 버그 수정: Jenkins가 멈췄다 재개돼도
    윈도우가 실제 시간 범위를 벗어나지 않는다.

    Args:
        snapshots: .timestamp/.probability를 가진 객체 리스트
        hours_back: 윈도우 크기 (시간)
        now: 기준 시각 (기본: utcnow, 테스트 주입용)

    Returns:
        윈도우 내 스냅샷 (timestamp 오름차순)
    """
    if now is None:
        now = datetime.utcnow()
    cutoff = now - timedelta(hours=hours_back)
    window = [s for s in snapshots if s.timestamp >= cutoff]
    return sorted(window, key=lambda s: s.timestamp)


def is_window_valid(
    window: Sequence,
    hours_back: float,
    min_points: int = WINDOW_MIN_POINTS,
    min_coverage: float = WINDOW_MIN_COVERAGE,
) -> bool:
    """윈도우가 판정에 쓸 만큼 데이터를 갖췄는지 검증.

    조건: 포인트 수 >= min_points AND
          (최신.ts - 최고(最古).ts) >= min_coverage * hours_back

    invalid면 백필 시도 → 그래도 invalid면 진입하지 않는다
    (banana의 관대한 cold-start 폴백 금지).
    """
    if len(window) < min_points:
        return False
    span_hours = (window[-1].timestamp - window[0].timestamp).total_seconds() / 3600.0
    return span_hours >= min_coverage * hours_back


def merge_price_points(db_points: Sequence, backfill_points: Sequence) -> List:
    """DB 스냅샷과 히스토리 백필 포인트 병합.

    분 단위로 반올림한 timestamp가 같으면 중복으로 보고 DB 스냅샷을 우선한다.

    Returns:
        timestamp 오름차순 병합 리스트
    """
    merged = {}
    # 백필 먼저 넣고 DB로 덮어써서 DB 우선
    for point in list(backfill_points) + list(db_points):
        key = point.timestamp.replace(second=0, microsecond=0)
        merged[key] = point
    return sorted(merged.values(), key=lambda p: p.timestamp)


def change_from_oldest(window: Sequence, current_price: float) -> Optional[float]:
    """윈도우 최고(最古) 스냅샷 대비 현재가 변화량. 윈도우가 비면 None."""
    if not window:
        return None
    return current_price - window[0].probability


def rise_from_low(window: Sequence, current_price: float) -> Optional[float]:
    """윈도우 내 최저가 대비 현재가 상승폭 (급등 감지용). 윈도우가 비면 None."""
    if not window:
        return None
    low = min(p.probability for p in window)
    return current_price - low


def evaluate_entry(
    *,
    yes_price: float,
    liquidity: float,
    volume_24h: float,
    hours_left: Optional[float],
    snapshots: Sequence,
    params: SignalParams,
    now: Optional[datetime] = None,
) -> EntrySignal:
    """Hope Crusher 진입 판정 (모두 충족해야 진입).

    1. liquidity >= min_liquidity (+ 옵션 volume_24h)
    2. entry_hours_min <= hours_left <= entry_hours_max
    3. YES 가격 ∈ [yes_min, yes_max] → NO 토큰 매수 대상
    4. 윈도우 유효성 (rise_lookback 기준, 백필 병합 후 스냅샷으로 판정)
    5. 사건 진행 배제 게이트:
       - YES 24h 변화 <= yes_rise_block_24h
       - 최근 6h YES 급등(저점 대비) < yes_spike_block_6h

    Args:
        yes_price: 현재 YES 가격
        snapshots: YES 가격 기준 스냅샷 리스트 (DB + 백필 병합본)
        now: 기준 시각 (테스트 주입용)

    Returns:
        EntrySignal(entry, reason)
    """
    if now is None:
        now = datetime.utcnow()

    # 1. 유동성 / 거래량
    if liquidity < params.min_liquidity:
        return EntrySignal(False, f"low_liquidity_{liquidity:.0f}")
    if params.min_volume_24h > 0 and volume_24h < params.min_volume_24h:
        return EntrySignal(False, f"low_volume_{volume_24h:.0f}")

    # 2. 시간 윈도우
    if hours_left is None:
        return EntrySignal(False, "no_end_date")
    if hours_left <= 0:
        return EntrySignal(False, "already_resolved")
    if hours_left < params.entry_hours_min:
        return EntrySignal(False, f"too_late_{hours_left:.1f}h")
    if hours_left > params.entry_hours_max:
        return EntrySignal(False, f"too_early_{hours_left:.1f}h")

    # 3. YES 롱샷 밴드 (= NO 매수 밴드 [1-yes_max, 1-yes_min])
    if not (params.yes_min <= yes_price <= params.yes_max):
        return EntrySignal(False, f"yes_out_of_band_{yes_price:.3f}")

    # 4. 윈도우 유효성 - invalid면 진입하지 않는다
    window_rise = get_window(snapshots, params.rise_lookback_hours, now=now)
    if not is_window_valid(window_rise, params.rise_lookback_hours):
        return EntrySignal(False, "window_invalid")

    # 5-a. 사건 진행 배제: YES 24h 변화 <= +yes_rise_block_24h
    change_24h = change_from_oldest(window_rise, yes_price)
    if change_24h is None or change_24h > params.yes_rise_block_24h + _EPS:
        change_str = f"{change_24h:+.3f}" if change_24h is not None else "n/a"
        return EntrySignal(False, f"yes_rising_24h_{change_str}")

    # 5-b. 사건 진행 배제: 최근 6h YES 급등(저점 대비) < yes_spike_block_6h
    window_spike = get_window(snapshots, params.spike_lookback_hours, now=now)
    spike_6h = rise_from_low(window_spike, yes_price)
    if spike_6h is None or spike_6h >= params.yes_spike_block_6h - _EPS:
        spike_str = f"{spike_6h:+.3f}" if spike_6h is not None else "n/a"
        return EntrySignal(False, f"yes_spike_6h_{spike_str}")

    return EntrySignal(
        True,
        f"hope_crusher_yes{yes_price:.2f}_{hours_left:.1f}h",
    )


def take_profit_target(
    buy_price: float,
    take_profit_percent: float,
    cap: float = TP_PRICE_CAP,
) -> float:
    """익절 목표가. buy_price*(1+tp)가 cap(0.99)을 넘으면 cap으로 제한.

    NO를 0.95에 사면 +6% 목표가는 1.007로 도달 불가 → 0.99 도달 시 익절
    (기존 봇의 take_profit 도달 불가 버그 수정).
    """
    return min(buy_price * (1 + take_profit_percent), cap)


def evaluate_exit(
    *,
    buy_price: float,
    current_price: float,
    hours_left: Optional[float],
    take_profit_percent: float,
    stop_loss_percent: float,
    exit_hours: float,
) -> ExitSignal:
    """Hope Crusher 청산 판정 (우선순위 순, trailing 없음).

    1. 손절: P&L <= stop_loss_percent (NO 하락 = YES 급등 = 사건 발생 신호)
    2. 익절: 현재가 >= 목표가 (0.99 캡)
    3. 시간 청산: 해결까지 exit_hours 미만

    Returns:
        ExitSignal(should_sell, reason)
    """
    pnl_percent = 0.0
    if buy_price and buy_price > 0:
        pnl_percent = (current_price - buy_price) / buy_price

    # 1. 손절
    if pnl_percent <= stop_loss_percent:
        return ExitSignal(True, "stop_loss")

    # 2. 익절 (0.99 캡)
    target = take_profit_target(buy_price, take_profit_percent)
    if current_price >= target - _EPS:
        return ExitSignal(True, "take_profit")

    # 3. 시간 청산
    if hours_left is not None and hours_left < exit_hours:
        return ExitSignal(True, "time_exit")

    return ExitSignal(False, "hold")
