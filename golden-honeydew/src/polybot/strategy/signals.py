"""Night Watch 전략 시그널 - 순수 함수 모음.

이 모듈은 DB/API에 의존하지 않는다. 스냅샷 리스트와 숫자만 입력받아
진입/청산 판정을 출력하므로 합성 데이터로 유닛테스트할 수 있다.
scanner/trader는 여기의 함수만 호출해 전략을 실행한다.

전략 요약 (STRATEGY.md 참고):
- 한산 시간대(UTC 06-13 또는 주말)에만 진입한다.
- 현재 YES 가격이 24h 스냅샷 중앙값(median)에서 ±0.05 이상 이탈했고,
  거래량 급증(진짜 뉴스)이 아니면 복원 방향으로 매수한다.
- dev < 0 → YES 매수(반등 기대), dev > 0 → NO 매수(페이드).
  NO 매수가는 실제 NO 토큰 가격(outcomePrices[1])을 사용한다 —
  얇은 호가에서는 YES+NO 합이 1이 아니므로 1-p 근사는 밴드 판정을 왜곡한다.
"""
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class SnapshotPoint:
    """스냅샷 1개 (DB MarketSnapshot 또는 히스토리 백필에서 변환).

    probability는 항상 YES 토큰 가격 기준이다 (Phase 0 저장 규칙과 동일).
    히스토리 백필 데이터는 volume_24h가 없으므로 None을 허용한다.
    """
    timestamp: datetime
    probability: float
    volume_24h: Optional[float] = None


@dataclass(frozen=True)
class NightWatchParams:
    """진입 판정 파라미터 (env > yaml > 기본값으로 결정된 값이 주입된다)."""
    median_lookback_hours: float = 24.0
    dev_min: float = 0.05
    vol_spike_block: float = 1.5
    vol_recent_hours: float = 3.0
    entry_prob_min: float = 0.30
    entry_prob_max: float = 0.90
    min_points: int = 5
    min_coverage: float = 0.5


@dataclass(frozen=True)
class ExitParams:
    """청산 판정 파라미터."""
    take_profit_percent: float = 0.06
    stop_loss_percent: float = -0.06
    max_holding_hours: float = 24.0
    exit_hours: float = 12.0


@dataclass(frozen=True)
class EntrySignal:
    """진입 판정 결과."""
    should_enter: bool
    reason: str
    side_index: Optional[int] = None    # 0=YES 토큰 매수, 1=NO 토큰 매수
    deviation: Optional[float] = None   # 현재 YES 가격 - 24h median
    median: Optional[float] = None
    buy_price: Optional[float] = None   # 매수할 토큰 기준 가격


# ---------------------------------------------------------------------------
# 시간 윈도우 (§3.2: banana의 "개수 기반 윈도우" 버그 수정 - timestamp 기반)
# ---------------------------------------------------------------------------

def get_window(
    snapshots: List[SnapshotPoint],
    hours_back: float,
    now: Optional[datetime] = None,
) -> List[SnapshotPoint]:
    """timestamp 기준으로 최근 hours_back 시간 내의 스냅샷만 필터링.

    Args:
        snapshots: 스냅샷 리스트 (정렬 무관)
        hours_back: 윈도우 크기 (시간)
        now: 기준 시각 (기본: 현재 UTC)

    Returns:
        시간순(오래된 것 먼저) 정렬된 윈도우
    """
    if now is None:
        now = datetime.utcnow()
    cutoff = now - timedelta(hours=hours_back)
    window = [s for s in snapshots if s.timestamp >= cutoff]
    return sorted(window, key=lambda s: s.timestamp)


def is_window_valid(
    window: List[SnapshotPoint],
    hours_back: float,
    min_points: int = 5,
    min_coverage: float = 0.5,
) -> bool:
    """윈도우가 통계적으로 신뢰 가능한지 검증.

    Jenkins가 멈췄다 재개되면 스냅샷 개수는 충분해도 실제 커버 시간이
    짧을 수 있다 (banana 버그). 두 조건을 모두 요구한다:
    1. 포인트 수 >= min_points
    2. (newest.ts - oldest.ts) >= min_coverage * hours_back

    Args:
        window: get_window 결과 (시간순 정렬 가정)
        hours_back: 윈도우 크기 (시간)
        min_points: 최소 스냅샷 수
        min_coverage: 최소 시간 커버리지 비율 (0.5 = 절반)

    Returns:
        유효하면 True
    """
    if len(window) < min_points:
        return False
    span_hours = (window[-1].timestamp - window[0].timestamp).total_seconds() / 3600
    return span_hours >= min_coverage * hours_back


def merge_snapshots(
    db_points: List[SnapshotPoint],
    backfill_points: List[SnapshotPoint],
) -> List[SnapshotPoint]:
    """DB 스냅샷과 히스토리 백필 데이터 병합 (중복 시각 제거).

    분 단위로 timestamp를 절삭해 중복을 판정하고, 충돌 시 DB 스냅샷을
    우선한다 (volume_24h 정보가 있으므로).

    Returns:
        시간순 정렬된 병합 리스트
    """
    merged = {}
    for point in backfill_points:
        key = point.timestamp.replace(second=0, microsecond=0)
        merged[key] = point
    for point in db_points:
        key = point.timestamp.replace(second=0, microsecond=0)
        merged[key] = point
    return sorted(merged.values(), key=lambda s: s.timestamp)


# ---------------------------------------------------------------------------
# 한산 시간대(quiet hours) 판정
# ---------------------------------------------------------------------------

def parse_quiet_hours(spec: str) -> Tuple[int, int]:
    """"6-13" 형식의 UTC 시간대 문자열 파싱.

    자정을 넘는 "22-4" 형식도 지원한다 (22시부터 다음날 4시까지).

    Args:
        spec: "시작시-종료시" (0~23, 종료시는 exclusive)

    Returns:
        (start_hour, end_hour) 튜플

    Raises:
        ValueError: 형식이 잘못됐거나 범위를 벗어난 경우
    """
    if not isinstance(spec, str):
        raise ValueError(f"quiet hours는 문자열이어야 함: {spec!r}")

    parts = spec.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"quiet hours 형식 오류 (예: '6-13'): {spec!r}")

    try:
        start = int(parts[0].strip())
        end = int(parts[1].strip())
    except ValueError:
        raise ValueError(f"quiet hours는 정수여야 함 (예: '6-13'): {spec!r}")

    if not (0 <= start <= 23) or not (0 <= end <= 23):
        raise ValueError(f"quiet hours는 0~23 범위여야 함: {spec!r}")

    if start == end:
        raise ValueError(f"quiet hours 시작과 끝이 같음 (빈 구간): {spec!r}")

    return start, end


def is_quiet_hour(now: datetime, quiet_range: Tuple[int, int]) -> bool:
    """현재 UTC 시각이 한산 시간대인지 판정.

    구간은 [start, end) — 시작 포함, 종료 미포함.
    start > end이면 자정을 넘는 구간으로 해석한다 (예: 22-4).

    Args:
        now: UTC 기준 datetime (naive UTC 가정)
        quiet_range: parse_quiet_hours 결과

    Returns:
        한산 시간대면 True
    """
    start, end = quiet_range
    hour = now.hour
    if start < end:
        return start <= hour < end
    # 자정을 넘는 구간 (예: 22-4 → 22,23,0,1,2,3시)
    return hour >= start or hour < end


def is_weekend(now: datetime) -> bool:
    """UTC 기준 주말(토/일) 판정."""
    return now.weekday() >= 5  # 5=토, 6=일


def is_quiet_time(
    now: datetime,
    quiet_range: Tuple[int, int],
    weekends_quiet: bool = True,
) -> bool:
    """진입 가능한 한산 시간대인지 종합 판정.

    weekends_quiet=True면 주말은 시각과 무관하게 항상 한산으로 본다.

    Args:
        now: UTC 기준 datetime
        quiet_range: (start_hour, end_hour)
        weekends_quiet: 주말 전체를 한산 시간대로 취급할지

    Returns:
        진입 가능 시간대면 True
    """
    if weekends_quiet and is_weekend(now):
        return True
    return is_quiet_hour(now, quiet_range)


# ---------------------------------------------------------------------------
# 진입 시그널
# ---------------------------------------------------------------------------

def compute_median_deviation(
    current_yes_price: float,
    window: List[SnapshotPoint],
) -> Tuple[Optional[float], Optional[float]]:
    """현재 YES 가격의 24h median 대비 편차 계산.

    Returns:
        (median, deviation) — 윈도우가 비어있으면 (None, None)
    """
    if not window:
        return None, None
    median = statistics.median(s.probability for s in window)
    return median, current_yes_price - median


def is_volume_spike(
    window: List[SnapshotPoint],
    now: datetime,
    recent_hours: float = 3.0,
    spike_mult: float = 1.5,
) -> bool:
    """거래량 급증(진짜 뉴스) 여부 판정.

    최근 recent_hours 스냅샷의 volume_24h 평균이 전체 윈도우 평균의
    spike_mult배 이상이면 True (뉴스에 의한 이탈 → 진입 금지).
    volume 데이터가 없으면(백필 전용 윈도우 등) 판정 불가 → False.

    Args:
        window: 24h 윈도우 스냅샷
        now: 기준 시각
        recent_hours: 최근 구간 크기 (시간)
        spike_mult: 급증 판정 배수

    Returns:
        거래량 급증이면 True
    """
    volumes = [
        (s.timestamp, s.volume_24h)
        for s in window
        if s.volume_24h is not None
    ]
    if not volumes:
        return False

    window_avg = statistics.mean(v for _, v in volumes)
    if window_avg <= 0:
        return False

    cutoff = now - timedelta(hours=recent_hours)
    recent = [v for ts, v in volumes if ts >= cutoff]
    if not recent:
        return False

    recent_avg = statistics.mean(recent)
    return recent_avg >= spike_mult * window_avg


def evaluate_entry(
    current_yes_price: float,
    snapshots: List[SnapshotPoint],
    now: datetime,
    params: NightWatchParams,
    current_no_price: Optional[float] = None,
) -> EntrySignal:
    """Night Watch 진입 판정 (한산 시간대 체크는 scanner가 사이클당 1회 수행).

    판정 순서:
    1. 윈도우 유효성 (timestamp 기반, §3.2)
    2. |현재 YES 가격 - 24h median| >= dev_min
    3. 거래량 급증 아님 (진짜 뉴스 배제)
    4. 복원 방향 결정: dev < 0 → YES 매수, dev > 0 → NO 매수
    5. 매수 토큰 가격이 [entry_prob_min, entry_prob_max] 구간

    Args:
        current_yes_price: 현재 YES 토큰 가격 (outcomePrices[0], 0.0~1.0)
        snapshots: YES 가격 기준 스냅샷 리스트 (DB + 백필 병합본)
        now: 기준 시각 (naive UTC)
        params: 전략 파라미터
        current_no_price: 현재 NO 토큰 가격 (outcomePrices[1]).
            None이면 1-p 근사로 폴백 (테스트/데이터 결손용) —
            실전 경로(scanner)는 항상 실제 가격을 전달한다

    Returns:
        EntrySignal (should_enter=False면 reason에 탈락 사유)
    """
    window = get_window(snapshots, params.median_lookback_hours, now)

    if not is_window_valid(
        window,
        params.median_lookback_hours,
        min_points=params.min_points,
        min_coverage=params.min_coverage,
    ):
        return EntrySignal(False, f"window_invalid_{len(window)}pts")

    median, deviation = compute_median_deviation(current_yes_price, window)

    # 부동소수점 오차 보정 (0.60 - 0.55 = 0.04999... 가 경계에서 탈락하지 않도록)
    if abs(deviation) + 1e-9 < params.dev_min:
        return EntrySignal(
            False, f"dev_below_min_{deviation:+.3f}",
            deviation=deviation, median=median,
        )

    if is_volume_spike(
        window, now,
        recent_hours=params.vol_recent_hours,
        spike_mult=params.vol_spike_block,
    ):
        return EntrySignal(
            False, "volume_spike_news",
            deviation=deviation, median=median,
        )

    # 복원 방향 매수: 하락 이탈 → YES 매수(반등), 상승 이탈 → NO 매수(페이드)
    if deviation < 0:
        side_index = 0
        buy_price = current_yes_price
    else:
        side_index = 1
        # 실제 NO 토큰 가격(outcomePrices[1]) 우선 — 1-p는 근사 폴백일 뿐
        buy_price = (
            current_no_price
            if current_no_price is not None
            else 1.0 - current_yes_price
        )

    if not (params.entry_prob_min <= buy_price <= params.entry_prob_max):
        return EntrySignal(
            False, f"price_out_of_band_{buy_price:.2f}",
            side_index=side_index, deviation=deviation,
            median=median, buy_price=buy_price,
        )

    return EntrySignal(
        True, f"night_dislocation_dev{deviation:+.3f}",
        side_index=side_index, deviation=deviation,
        median=median, buy_price=buy_price,
    )


# ---------------------------------------------------------------------------
# 청산 시그널
# ---------------------------------------------------------------------------

def take_profit_target(buy_price: float, take_profit_percent: float) -> float:
    """익절 목표가 계산. 0.99를 넘으면 0.99로 캡 (§3.5 도달 불가 수정)."""
    return min(buy_price * (1 + take_profit_percent), 0.99)


def evaluate_exit(
    buy_price: float,
    current_price: float,
    buy_timestamp: Optional[datetime],
    market_end_date: Optional[datetime],
    now: datetime,
    params: ExitParams,
) -> Tuple[bool, str]:
    """청산 판정 (우선순위: SL → TP → max_holding → time_exit).

    Night Watch는 빠른 회전 전략이므로 trailing stop이 없다.

    Args:
        buy_price: 매수가
        current_price: 현재 midpoint
        buy_timestamp: 매수 시각 (naive UTC)
        market_end_date: 시장 해결 예정 시각 (naive UTC 또는 None)
        now: 기준 시각 (naive UTC)
        params: 청산 파라미터

    Returns:
        (should_sell, exit_reason) — 보유 유지면 (False, "hold")
    """
    if buy_price <= 0:
        return False, "hold"

    pnl_percent = (current_price - buy_price) / buy_price

    # 1. 손절
    if pnl_percent <= params.stop_loss_percent:
        return True, "stop_loss"

    # 2. 익절 (0.99 캡 적용)
    if current_price >= take_profit_target(buy_price, params.take_profit_percent):
        return True, "take_profit"

    # 3. 최대 보유 시간 초과 (복원 실패 → 회전)
    if buy_timestamp is not None:
        holding_hours = (now - buy_timestamp).total_seconds() / 3600
        if holding_hours >= params.max_holding_hours:
            return True, "max_holding"

    # 4. 해결 임박 청산
    if market_end_date is not None:
        end = market_end_date
        if end.tzinfo is not None:
            # 단순 replace(tzinfo=None)는 비UTC aware 입력에서 offset만큼 오차가 난다
            end = end.astimezone(timezone.utc).replace(tzinfo=None)
        hours_left = (end - now).total_seconds() / 3600
        if hours_left < params.exit_hours:
            return True, "time_exit"

    return False, "hold"
