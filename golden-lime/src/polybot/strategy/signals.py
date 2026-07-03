"""Shock Follow 전략 시그널 - 순수 함수 모음.

모든 함수는 스냅샷 리스트/숫자를 입력받아 판정만 출력한다 (DB/API 의존 없음).
scanner(진입)와 trader(청산)가 이 모듈을 호출하며, 전략 변경은 여기만 수정하면 된다.

용어:
- series: 매수 후보 토큰 기준 가격 시계열. 스냅샷은 항상 YES(index 0) 가격으로
  저장되므로 NO 방향 평가 시 invert_series()로 1-p 변환한다.
- 점프 감지는 "윈도우 내 최저가 대비 현재가" 기준으로 단순·견고하게 판정한다.
  (스냅샷 간격 불균일/결손에 강하다)
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence

TAKE_PROFIT_PRICE_CAP = 0.99

# 부동소수점 경계 비교 오차 허용치 (0.50-0.40=0.0999... 같은 표현 오차 흡수)
EPSILON = 1e-9


@dataclass(frozen=True)
class PricePoint:
    """토큰 기준 가격 1포인트 (DB 스냅샷 또는 prices-history 백필 캔들)."""
    timestamp: datetime
    price: float
    volume_24h: Optional[float] = None


@dataclass(frozen=True)
class ShockParams:
    """Shock Follow 판정 파라미터 (기본값 = env/yaml 기본값과 동일)."""
    jump_window_hours: float = 6.0
    jump_min: float = 0.10
    base_min: float = 0.15
    base_max: float = 0.70
    current_max: float = 0.85
    hold_window_minutes: float = 60.0
    max_pullback: float = 0.02
    vol_mult_min: float = 2.0
    death_window_hours: float = 3.0
    vol_lookback_hours: float = 24.0
    min_window_points: int = 5
    min_window_coverage: float = 0.5


@dataclass(frozen=True)
class JumpSignal:
    """점프 감지 결과."""
    jump_size: float
    base_price: float


@dataclass(frozen=True)
class EntryDecision:
    """진입 판정 결과."""
    enter: bool
    outcome_index: Optional[int]  # 0=YES, 1=NO, None=방향 없음
    reason: str
    jump_size: Optional[float] = None
    base_price: Optional[float] = None
    token_price: Optional[float] = None


def to_price_points(snapshots: Sequence) -> List[PricePoint]:
    """DB MarketSnapshot 리스트 → PricePoint 리스트 (YES 가격 기준)."""
    return [
        PricePoint(
            timestamp=s.timestamp,
            price=float(s.probability),
            volume_24h=float(s.volume_24h) if s.volume_24h is not None else None,
        )
        for s in snapshots
    ]


def invert_series(points: Sequence[PricePoint]) -> List[PricePoint]:
    """YES 가격 시계열 → NO 토큰 가격 시계열 (1-p). volume은 시장 공통이라 유지."""
    return [
        PricePoint(p.timestamp, round(1.0 - p.price, 6), p.volume_24h)
        for p in points
    ]


def merge_price_points(
    db_points: Sequence[PricePoint],
    backfill_points: Optional[Sequence[PricePoint]],
) -> List[PricePoint]:
    """DB 스냅샷과 백필 포인트 병합 (분 단위 중복 시각 제거, DB 우선)."""
    merged = list(db_points)
    seen = {p.timestamp.replace(second=0, microsecond=0) for p in db_points}
    for p in backfill_points or []:
        key = p.timestamp.replace(second=0, microsecond=0)
        if key not in seen:
            merged.append(p)
            seen.add(key)
    merged.sort(key=lambda p: p.timestamp)
    return merged


def get_window(
    points: Sequence[PricePoint],
    hours_back: float,
    now: Optional[datetime] = None,
) -> List[PricePoint]:
    """timestamp 기반 윈도우 추출: ts >= now - hours_back (시간순 정렬).

    banana의 '스냅샷 개수' 윈도우는 Jenkins 중단 시 몇 시간을 커버하는 버그가
    있었다 - 신규 봇은 반드시 timestamp로 자른다.
    """
    if now is None:
        now = datetime.utcnow()
    cutoff = now - timedelta(hours=hours_back)
    window = [p for p in points if p.timestamp >= cutoff]
    window.sort(key=lambda p: p.timestamp)
    return window


def is_window_valid(
    window: Sequence[PricePoint],
    hours_back: float,
    min_points: int = 5,
    min_coverage: float = 0.5,
) -> bool:
    """윈도우 유효성: 포인트 수 + 실제 커버 시간 범위 검증.

    조건: len >= min_points AND (newest.ts - oldest.ts) >= min_coverage * hours_back
    invalid면 진입하지 않는다 (banana의 관대한 cold-start 폴백 금지).
    """
    if len(window) < min_points:
        return False
    span_hours = (window[-1].timestamp - window[0].timestamp).total_seconds() / 3600
    return span_hours >= min_coverage * hours_back


def detect_jump(
    window: Sequence[PricePoint],
    current_price: float,
    jump_min: float,
    base_min: float,
    base_max: float,
    current_max: float,
) -> Optional[JumpSignal]:
    """점프 감지: 윈도우 내 최저가(base) 대비 현재가 상승폭.

    - jump = current - base >= jump_min
    - base ∈ [base_min, base_max] (이미 극단가였던 시장 배제)
    - current <= current_max (러닝룸 확보)
    """
    if not window:
        return None
    base = min(p.price for p in window)
    jump = current_price - base
    if jump < jump_min - EPSILON:
        return None
    if not (base_min - EPSILON <= base <= base_max + EPSILON):
        return None
    if current_price > current_max + EPSILON:
        return None
    return JumpSignal(jump_size=jump, base_price=base)


def is_holding_high(
    window: Sequence[PricePoint],
    current_price: float,
    hold_window_minutes: float,
    max_pullback: float,
    now: Optional[datetime] = None,
    min_points: int = 2,
) -> bool:
    """고점 유지 확인: 최근 hold_window 분의 고점 대비 되돌림 <= max_pullback.

    되돌림이 크면 '노이즈 스파이크'(elderberry의 영역)이므로 편승하지 않는다.
    최근 포인트가 min_points 미만이면 판단 불가 → 보수적으로 False.
    """
    if now is None:
        now = datetime.utcnow()
    cutoff = now - timedelta(minutes=hold_window_minutes)
    recent = [p for p in window if p.timestamp >= cutoff]
    if len(recent) < min_points:
        return False
    peak = max(max(p.price for p in recent), current_price)
    return (peak - current_price) <= max_pullback + EPSILON


def is_volume_confirmed(
    volumes: Sequence[Optional[float]],
    current_volume_24h: Optional[float],
    vol_mult_min: float,
) -> bool:
    """거래량 확인: 현재 volume24hr >= 윈도우 평균 x vol_mult_min.

    거래량 폭증 = '진짜 정보'의 신호. 백필은 volume을 제공하지 않으므로
    이 게이트는 실제 축적된 스냅샷이 있어야 통과할 수 있다 (의도된 보수성).
    """
    vols = [float(v) for v in volumes if v is not None and float(v) > 0]
    if not vols or not current_volume_24h or current_volume_24h <= 0:
        return False
    avg = sum(vols) / len(vols)
    return current_volume_24h >= vol_mult_min * avg


def evaluate_entry(
    yes_points: Sequence[PricePoint],
    current_yes_price: float,
    current_volume_24h: Optional[float],
    params: ShockParams,
    now: Optional[datetime] = None,
) -> EntryDecision:
    """Shock Follow 진입 종합 판정 (순수 함수).

    방향: YES 급등 → YES(index 0) 매수, YES 급락(=NO 급등) → NO(index 1) 매수.
    두 방향 모두 '매수할 토큰 기준' 가격으로 같은 규칙을 적용한다.
    YES 방향을 먼저 평가하며, 점프가 감지된 방향에서 후속 게이트가 실패하면
    그 사유로 종료한다 (같은 사이클에 양방향 점프가 동시 성립하는 경우는 드물다).
    """
    if now is None:
        now = datetime.utcnow()

    # 윈도우 유효성은 timestamp 기준이라 방향과 무관 - YES 시계열로 1회만 검사
    yes_window = get_window(yes_points, params.jump_window_hours, now)
    if not is_window_valid(
        yes_window,
        params.jump_window_hours,
        params.min_window_points,
        params.min_window_coverage,
    ):
        return EntryDecision(False, None, "window_invalid")

    directions = [
        (0, yes_window, current_yes_price, "jump_up"),
        (1, invert_series(yes_window), round(1.0 - current_yes_price, 6), "jump_down"),
    ]

    for outcome_index, window, token_price, tag in directions:
        jump = detect_jump(
            window,
            token_price,
            params.jump_min,
            params.base_min,
            params.base_max,
            params.current_max,
        )
        if jump is None:
            continue

        if not is_holding_high(
            window,
            token_price,
            params.hold_window_minutes,
            params.max_pullback,
            now,
        ):
            return EntryDecision(
                False, outcome_index, "pullback_too_deep",
                jump.jump_size, jump.base_price, token_price,
            )

        vol_window = get_window(yes_points, params.vol_lookback_hours, now)
        volumes = [p.volume_24h for p in vol_window]
        if not is_volume_confirmed(volumes, current_volume_24h, params.vol_mult_min):
            return EntryDecision(
                False, outcome_index, "volume_unconfirmed",
                jump.jump_size, jump.base_price, token_price,
            )

        return EntryDecision(
            True, outcome_index, tag,
            jump.jump_size, jump.base_price, token_price,
        )

    return EntryDecision(False, None, "no_jump")


def is_momentum_dead(
    points: Sequence[PricePoint],
    current_price: float,
    death_window_hours: float,
    now: Optional[datetime] = None,
    min_coverage: float = 0.5,
) -> bool:
    """모멘텀 사망 판정 (청산 조건): 최근 death_window 동안 가격 변화 <= 0.

    윈도우 데이터가 얕으면(포인트 2개 미만 또는 커버리지 부족) 판단을 보류하고
    False를 반환한다 - 매수 직후 스냅샷 1~2개로 즉시 청산되는 것을 방지.
    """
    if now is None:
        now = datetime.utcnow()
    window = get_window(points, death_window_hours, now)
    if len(window) < 2:
        return False
    oldest_age_hours = (now - window[0].timestamp).total_seconds() / 3600
    if oldest_age_hours < min_coverage * death_window_hours:
        return False
    return (current_price - window[0].price) <= 0.0


def capped_take_profit_target(
    buy_price: float,
    take_profit_percent: float,
    cap: float = TAKE_PROFIT_PRICE_CAP,
) -> float:
    """익절 목표가 계산: buy*(1+tp)가 0.99를 넘으면 0.99로 캡.

    캡이 없으면 고가 진입 건은 목표가가 1.0을 넘어 영구 도달 불가가 된다
    (cherry의 확인된 버그 수정).
    """
    return min(buy_price * (1.0 + take_profit_percent), cap)
