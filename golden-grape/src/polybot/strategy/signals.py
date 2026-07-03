"""Cascade Rider 전략 시그널 - 순수 함수 모음.

이 모듈은 I/O가 없다. 스냅샷 리스트/숫자를 입력받아 판정을 출력한다.
scanner/trader가 호출하며, 유닛테스트의 대상이다.

스냅샷 규약:
- `timestamp`(naive UTC datetime), `probability`(YES 가격), `volume_24h` 속성을 가진
  객체면 무엇이든 받는다 (SQLAlchemy MarketSnapshot, SnapshotPoint 모두 가능).
- 저장 기준은 항상 YES 가격이다. NO side 판정 시 `to_token_points`로 1-p 변환한다.

banana의 골든크로스 실패 교정 (STRATEGY.md 참고):
- 개수 기반 윈도우 → timestamp 기반 윈도우 + 커버리지 검증 (`get_window`/`is_window_valid`)
- 도달 불가 threshold(스냅샷당 기울기) → 윈도우 전체 변화량(%p) 밴드 (`drift_min~drift_max`)
- 관대한 cold-start 폴백 → 윈도우 invalid면 진입하지 않음 (백필은 scanner가 시도)
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence

# float 뺄셈 노이즈로 경계값 판정이 흔들리지 않도록 하는 허용 오차
EPSILON = 1e-9


@dataclass(frozen=True)
class SnapshotPoint:
    """DB 스냅샷/히스토리 백필 공용 경량 포인트 (YES 가격 기준)."""
    timestamp: datetime
    probability: float
    volume_24h: Optional[float] = None


@dataclass(frozen=True)
class EntryDecision:
    """진입 판정 결과."""
    should_enter: bool
    reason: str
    side: Optional[str] = None          # "Yes" or "No"
    token_price: Optional[float] = None # 매수 토큰 기준 현재가
    drift: Optional[float] = None       # 매수 토큰 기준 24h 드리프트
    consistency: Optional[float] = None # 비음 버킷 비율
    vol_accel: Optional[float] = None   # 거래량 가속 배수


def _utcnow() -> datetime:
    return datetime.utcnow()


def get_window(
    snapshots: Sequence,
    hours_back: float,
    now: Optional[datetime] = None,
) -> List:
    """timestamp 기반 윈도우 추출: ts >= now - hours_back 필터.

    banana의 "개수 기반 윈도우" 버그 수정 - Jenkins가 멈췄다 재개되어도
    윈도우가 의도한 시간 범위만 커버한다.

    Returns:
        시간 오름차순 정렬된 스냅샷 리스트
    """
    now = now or _utcnow()
    cutoff = now - timedelta(hours=hours_back)
    window = [s for s in snapshots if cutoff <= s.timestamp <= now]
    window.sort(key=lambda s: s.timestamp)
    return window


def is_window_valid(
    window: Sequence,
    hours_back: float,
    min_points: int = 5,
    min_coverage: float = 0.5,
) -> bool:
    """윈도우 유효성 검증.

    조건: len(window) >= min_points AND
          (newest.ts - oldest.ts) >= min_coverage * hours_back
    """
    if len(window) < min_points:
        return False
    span = window[-1].timestamp - window[0].timestamp
    return span >= timedelta(hours=min_coverage * hours_back)


def merge_snapshots(primary: Sequence, secondary: Sequence) -> List[SnapshotPoint]:
    """DB 스냅샷(primary)과 백필 포인트(secondary) 병합.

    중복 시각(분 단위 반올림)은 primary 우선으로 제거한다.

    Returns:
        시간 오름차순 정렬된 SnapshotPoint 리스트
    """
    merged = {}
    for s in secondary:
        key = s.timestamp.replace(second=0, microsecond=0)
        merged[key] = SnapshotPoint(s.timestamp, s.probability, s.volume_24h)
    for s in primary:
        key = s.timestamp.replace(second=0, microsecond=0)
        merged[key] = SnapshotPoint(s.timestamp, s.probability, s.volume_24h)
    return sorted(merged.values(), key=lambda s: s.timestamp)


def to_token_points(snapshots: Sequence, token_index: int) -> List[SnapshotPoint]:
    """YES 가격 스냅샷을 매수 토큰 기준 가격으로 변환.

    token_index 0(YES)이면 그대로, 1(NO)이면 1-p로 변환한다.
    """
    if token_index == 0:
        return [
            SnapshotPoint(s.timestamp, s.probability, s.volume_24h)
            for s in snapshots
        ]
    return [
        SnapshotPoint(s.timestamp, 1.0 - s.probability, s.volume_24h)
        for s in snapshots
    ]


def compute_bucket_consistency(
    points: Sequence,
    lookback_hours: int,
    bucket_hours: int,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """드리프트 일관성: lookback을 bucket_hours 단위로 나눠
    비음(변화 >= 0) 버킷의 비율을 계산한다.

    각 버킷의 변화 = 버킷 내 (마지막 가격 - 첫 가격). 포인트 2개 미만인
    버킷은 판정에서 제외한다. 판정 가능한 버킷이 전체의 절반 미만이면
    데이터 부족으로 None을 반환한다 (진입 금지).

    Returns:
        비음 버킷 비율 (0.0~1.0) 또는 None (데이터 부족)
    """
    if bucket_hours <= 0 or lookback_hours < bucket_hours:
        return None
    now = now or _utcnow()
    n_buckets = lookback_hours // bucket_hours

    buckets: List[List] = [[] for _ in range(n_buckets)]
    for pt in points:
        age_hours = (now - pt.timestamp).total_seconds() / 3600
        if age_hours < 0 or age_hours > lookback_hours:
            continue
        idx = min(int(age_hours // bucket_hours), n_buckets - 1)
        buckets[idx].append(pt)

    changes = []
    for bucket in buckets:
        if len(bucket) < 2:
            continue
        bucket.sort(key=lambda p: p.timestamp)
        changes.append(bucket[-1].probability - bucket[0].probability)

    if len(changes) < max(2, n_buckets // 2):
        return None

    non_negative = sum(1 for c in changes if c >= 0)
    return non_negative / len(changes)


def compute_volume_acceleration(
    current_volume_24h: float,
    window: Sequence,
) -> Optional[float]:
    """거래량 가속 배수: 현재 volume24hr / 윈도우 평균 volume_24h.

    Returns:
        가속 배수 또는 None (윈도우에 유효한 거래량 데이터 없음)
    """
    volumes = [
        s.volume_24h for s in window
        if s.volume_24h is not None and s.volume_24h > 0
    ]
    if not volumes or current_volume_24h is None:
        return None
    avg = sum(volumes) / len(volumes)
    if avg <= 0:
        return None
    return current_volume_24h / avg


def take_profit_target(
    buy_price: float,
    take_profit_percent: float,
    cap: float = 0.99,
) -> float:
    """익절 목표가. buy_price*(1+tp)가 cap을 넘으면 cap으로 캡.

    banana/cherry의 "TP 도달 불가" 버그 수정 - 0.99 도달 시 익절한다.
    """
    return min(buy_price * (1 + take_profit_percent), cap)


def evaluate_entry(
    snapshots: Sequence,
    yes_price: float,
    volume_24h: float,
    *,
    prob_min: float = 0.40,
    prob_max: float = 0.80,
    drift_lookback_hours: int = 24,
    drift_min: float = 0.04,
    drift_max: float = 0.10,
    bucket_hours: int = 4,
    consistency_min: float = 0.70,
    vol_accel_min: float = 1.2,
    min_points: int = 5,
    min_coverage: float = 0.5,
    now: Optional[datetime] = None,
) -> EntryDecision:
    """Cascade Rider 진입 판정 (순수 함수).

    입력 스냅샷은 YES 가격 기준. 판정 순서:
    1. 윈도우 유효성 (invalid면 진입하지 않음 - cold-start 폴백 없음)
    2. 드리프트 방향 결정 (YES 상승 → YES 매수, YES 하락 → NO 매수)
    3. 매수 토큰 기준 드리프트 밴드 [drift_min, drift_max]
    4. 매수 토큰 기준 가격 밴드 [prob_min, prob_max]
    5. 버킷 일관성 >= consistency_min
    6. 거래량 가속 >= vol_accel_min

    Args:
        snapshots: YES 가격 스냅샷 리스트 (백필 병합 후)
        yes_price: 현재 YES 가격 (gamma outcomePrices[0])
        volume_24h: 현재 24h 거래량 (gamma volume24hr)

    Returns:
        EntryDecision
    """
    now = now or _utcnow()

    window = get_window(snapshots, drift_lookback_hours, now)
    if not is_window_valid(window, drift_lookback_hours, min_points, min_coverage):
        return EntryDecision(False, "window_invalid")

    yes_drift = yes_price - window[0].probability
    if yes_drift > 0:
        side, token_index = "Yes", 0
        token_price = yes_price
    elif yes_drift < 0:
        side, token_index = "No", 1
        token_price = 1.0 - yes_price
    else:
        return EntryDecision(False, "no_drift")

    # 매수 토큰 기준 드리프트 (방향 전환 후 항상 양수)
    token_points = to_token_points(window, token_index)
    drift = token_price - token_points[0].probability

    if drift < drift_min - EPSILON:
        return EntryDecision(
            False, f"drift_too_small_{drift:+.3f}",
            side=side, token_price=token_price, drift=drift,
        )
    if drift > drift_max + EPSILON:
        # 리서치 근거: 10%+ 급변은 mean-revert 영역
        return EntryDecision(
            False, f"drift_too_large_{drift:+.3f}",
            side=side, token_price=token_price, drift=drift,
        )

    if not (prob_min - EPSILON <= token_price <= prob_max + EPSILON):
        return EntryDecision(
            False, f"price_out_of_band_{token_price:.2f}",
            side=side, token_price=token_price, drift=drift,
        )

    consistency = compute_bucket_consistency(
        token_points, drift_lookback_hours, bucket_hours, now
    )
    if consistency is None:
        return EntryDecision(
            False, "insufficient_bucket_data",
            side=side, token_price=token_price, drift=drift,
        )
    if consistency < consistency_min - EPSILON:
        return EntryDecision(
            False, f"inconsistent_drift_{consistency:.2f}",
            side=side, token_price=token_price, drift=drift,
            consistency=consistency,
        )

    vol_accel = compute_volume_acceleration(volume_24h, window)
    if vol_accel is None:
        return EntryDecision(
            False, "no_volume_data",
            side=side, token_price=token_price, drift=drift,
            consistency=consistency,
        )
    if vol_accel < vol_accel_min - EPSILON:
        return EntryDecision(
            False, f"vol_accel_too_low_{vol_accel:.2f}",
            side=side, token_price=token_price, drift=drift,
            consistency=consistency, vol_accel=vol_accel,
        )

    reason = "cascade_up" if side == "Yes" else "cascade_down"
    return EntryDecision(
        True, reason,
        side=side, token_price=token_price, drift=drift,
        consistency=consistency, vol_accel=vol_accel,
    )


def is_drift_dead(
    snapshots: Sequence,
    token_index: int,
    death_window_hours: int = 6,
    now: Optional[datetime] = None,
) -> Optional[bool]:
    """드리프트 소멸 판정: 최근 death_window_hours 동안
    매수 토큰 기준 가격 변화 <= 0이면 True.

    Returns:
        True(소멸) / False(지속) / None(포인트 2개 미만 - 판단 불가)
    """
    now = now or _utcnow()
    window = get_window(snapshots, death_window_hours, now)
    if len(window) < 2:
        return None
    token_points = to_token_points(window, token_index)
    change = token_points[-1].probability - token_points[0].probability
    return change <= 0
