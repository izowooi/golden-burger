"""Panic Fade 전략 시그널 - 순수 함수 모음.

이 모듈은 DB/API에 의존하지 않는다. 스냅샷 리스트와 숫자만 입력받아
진입/청산 판정을 출력한다. scanner/trader가 이 함수들을 호출하며,
전략을 바꾸려면 이 파일만 수정하면 된다.

스냅샷 히스토리는 항상 YES 가격 기준으로 저장된다.
favorite 판별은 "ref 시점(급락 전 최고가 시점)에 favorite이었던 쪽":
ref 윈도우의 YES 최고가 >= 0.5면 YES쪽을, 아니면 NO쪽(1-p 환산)을 평가한다.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import pstdev
from typing import List, NamedTuple, Optional


class PricePoint(NamedTuple):
    """가격 시계열 한 점 (timestamp: naive UTC, price: YES 가격 0.0~1.0)."""
    timestamp: datetime
    price: float


# ---------------------------------------------------------------------------
# 시간 검증된 스냅샷 윈도우 (banana의 개수 기반 윈도우 버그 수정)
# ---------------------------------------------------------------------------

def get_window(
    snapshots: List[PricePoint],
    hours_back: float,
    now: Optional[datetime] = None,
) -> List[PricePoint]:
    """timestamp 기준으로 최근 hours_back 시간 이내의 스냅샷만 필터.

    개수 기반이 아닌 timestamp 기반 윈도우 - Jenkins가 멈췄다 재개돼도
    "48시간 윈도우"가 실제 48시간을 벗어나지 않는다.

    Args:
        snapshots: 가격 시계열 (정렬 무관)
        hours_back: 윈도우 크기 (시간)
        now: 기준 시각 (기본: utcnow)

    Returns:
        시간순(오래된 것 먼저) 정렬된 윈도우 내 스냅샷 리스트
    """
    if now is None:
        now = datetime.utcnow()
    cutoff = now - timedelta(hours=hours_back)
    window = [p for p in snapshots if p.timestamp >= cutoff]
    return sorted(window, key=lambda p: p.timestamp)


def is_window_valid(
    window: List[PricePoint],
    hours_back: float,
    min_points: int = 5,
    min_coverage: float = 0.5,
) -> bool:
    """윈도우가 신호 계산에 충분한 데이터를 갖는지 검증.

    조건: 포인트 수 >= min_points AND
          (최신 ts - 최고(最古) ts) >= min_coverage * hours_back

    invalid면 진입하지 않는다 (banana의 관대한 cold-start 폴백 금지).
    """
    if len(window) < min_points:
        return False
    span = window[-1].timestamp - window[0].timestamp
    required = timedelta(hours=hours_back * min_coverage)
    return span >= required


def merge_price_series(
    db_points: List[PricePoint],
    backfill_points: List[PricePoint],
) -> List[PricePoint]:
    """DB 스냅샷과 히스토리 백필 포인트 병합 (분 단위 중복 시각 제거).

    같은 분(minute)에 두 소스가 겹치면 DB 스냅샷을 우선한다.
    """
    merged = {}
    for p in backfill_points:
        key = p.timestamp.replace(second=0, microsecond=0)
        merged[key] = p
    for p in db_points:
        key = p.timestamp.replace(second=0, microsecond=0)
        merged[key] = p
    return sorted(merged.values(), key=lambda p: p.timestamp)


# ---------------------------------------------------------------------------
# Panic Fade 진입 시그널
# ---------------------------------------------------------------------------

@dataclass
class PanicFadeParams:
    """Panic Fade 진입 파라미터 (config에서 주입)."""
    ref_window_hours: float = 48.0
    ref_exclude_recent_hours: float = 3.0
    ref_min: float = 0.70
    drop_min: float = 0.12
    current_min: float = 0.35
    current_max: float = 0.75
    stab_window_minutes: float = 45.0
    stab_min_points: int = 3
    stab_max_std: float = 0.02
    window_min_points: int = 5
    window_min_coverage: float = 0.5


@dataclass
class PanicFadeSignal:
    """진입 판정 결과."""
    entry: bool
    reason: str
    side: Optional[str] = None          # "Yes" 또는 "No"
    token_index: Optional[int] = None   # 0=Yes, 1=No
    ref_price: Optional[float] = None   # 매수 토큰 기준 급락 전 최고가
    current_price: Optional[float] = None  # 매수 토큰 기준 현재가
    drop: Optional[float] = None        # ref - current
    stab_std: Optional[float] = None    # 안정화 구간 표준편차


def evaluate_panic_fade(
    yes_snapshots: List[PricePoint],
    yes_price_now: float,
    params: PanicFadeParams,
    now: Optional[datetime] = None,
) -> PanicFadeSignal:
    """Panic Fade 진입 시그널 판정 (순수 함수).

    진입 조건 (모두 충족, favorite였던 쪽 토큰 기준 가격 p):
    1. 윈도우 유효성 통과 (timestamp 기반)
    2. 기준가 ref = 최근 48h 윈도우(단 최근 3h 제외)의 최고가; ref >= 0.70
    3. 낙폭: ref - p >= 0.12
    4. 붕괴 배제: 0.35 <= p <= 0.75
    5. 바닥 안정화: 최근 45분(>=3 스냅샷)에서 직전 스냅샷들(최신 포인트 제외)의
       min 가격보다 현재가가 낮지 않고(신저가 금지), 그 구간 std <= 0.02

    Args:
        yes_snapshots: YES 가격 기준 스냅샷 시계열
        yes_price_now: 현재 YES 가격
        params: 전략 파라미터
        now: 기준 시각 (기본: utcnow)

    Returns:
        PanicFadeSignal (entry=True면 side/token_index가 매수 대상)
    """
    if now is None:
        now = datetime.utcnow()

    # 1. 윈도우 유효성 (timestamp 기반)
    window = get_window(yes_snapshots, params.ref_window_hours, now)
    if not is_window_valid(
        window,
        params.ref_window_hours,
        min_points=params.window_min_points,
        min_coverage=params.window_min_coverage,
    ):
        return PanicFadeSignal(entry=False, reason="window_invalid")

    # 2. ref 윈도우 = 최근 ref_exclude_recent_hours 제외 구간
    ref_cutoff = now - timedelta(hours=params.ref_exclude_recent_hours)
    ref_window = [p for p in window if p.timestamp <= ref_cutoff]
    if not ref_window:
        return PanicFadeSignal(entry=False, reason="no_ref_data")

    # favorite 판별: ref 시점에 favorite이었던 쪽
    # YES 최고가 >= 0.5면 YES쪽, 아니면 NO쪽(1-p 환산) 평가
    ref_yes_max = max(p.price for p in ref_window)
    if ref_yes_max >= 0.5:
        side, token_index = "Yes", 0
        series = window
        ref_price = ref_yes_max
        price_now = yes_price_now
    else:
        side, token_index = "No", 1
        series = [PricePoint(p.timestamp, 1.0 - p.price) for p in window]
        ref_price = max(1.0 - p.price for p in ref_window)
        price_now = 1.0 - yes_price_now

    # 2b. 원래 favorite이었어야 함
    if ref_price < params.ref_min:
        return PanicFadeSignal(
            entry=False, reason=f"ref_below_min_{ref_price:.2f}",
            side=side, token_index=token_index,
            ref_price=ref_price, current_price=price_now,
        )

    # 3. 낙폭 확인
    drop = ref_price - price_now
    if drop < params.drop_min:
        return PanicFadeSignal(
            entry=False, reason=f"drop_too_small_{drop:.3f}",
            side=side, token_index=token_index,
            ref_price=ref_price, current_price=price_now, drop=drop,
        )

    # 4. 붕괴 배제 (완전 붕괴 시장은 '진짜 정보'일 가능성이 높음)
    if not (params.current_min <= price_now <= params.current_max):
        return PanicFadeSignal(
            entry=False, reason=f"price_out_of_band_{price_now:.2f}",
            side=side, token_index=token_index,
            ref_price=ref_price, current_price=price_now, drop=drop,
        )

    # 5. 바닥 안정화 (떨어지는 칼날 회피)
    stab_cutoff = now - timedelta(minutes=params.stab_window_minutes)
    stab_points = [p for p in series if p.timestamp >= stab_cutoff]
    if len(stab_points) < params.stab_min_points:
        return PanicFadeSignal(
            entry=False, reason="stab_insufficient_data",
            side=side, token_index=token_index,
            ref_price=ref_price, current_price=price_now, drop=drop,
        )

    # 신저가 금지: min은 최신 포인트를 제외한 "직전" 스냅샷들로 계산한다.
    # 운영에서는 Phase 0이 현재가를 스냅샷으로 먼저 저장하므로 현재가 자신이
    # 항상 윈도우에 포함된다 - 포함한 채 min을 구하면 이 게이트는 절대
    # 발동하지 않아(항상 min <= 현재가) 하락 진행 중에도 진입해 버린다.
    prior_points = stab_points[:-1]
    if prior_points:
        stab_min = min(p.price for p in prior_points)
        if price_now < stab_min:
            return PanicFadeSignal(
                entry=False, reason="still_falling",
                side=side, token_index=token_index,
                ref_price=ref_price, current_price=price_now, drop=drop,
            )

    stab_std = pstdev(p.price for p in stab_points)
    if stab_std > params.stab_max_std:
        return PanicFadeSignal(
            entry=False, reason=f"not_stabilized_std_{stab_std:.4f}",
            side=side, token_index=token_index,
            ref_price=ref_price, current_price=price_now,
            drop=drop, stab_std=stab_std,
        )

    return PanicFadeSignal(
        entry=True,
        reason=f"panic_fade_ref{ref_price:.2f}_drop{drop:.2f}",
        side=side, token_index=token_index,
        ref_price=ref_price, current_price=price_now,
        drop=drop, stab_std=stab_std,
    )


# ---------------------------------------------------------------------------
# 청산 시그널
# ---------------------------------------------------------------------------

# take_profit 목표가 캡 - buy_price*(1+tp)가 0.99를 넘으면 0.99 도달 시 익절
TAKE_PROFIT_PRICE_CAP = 0.99


def evaluate_exit(
    buy_price: float,
    current_price: float,
    take_profit_percent: float,
    stop_loss_percent: float,
    holding_hours: Optional[float],
    max_holding_hours: float,
    hours_left: Optional[float],
    exit_hours: float,
) -> tuple[bool, str]:
    """청산 판정 (우선순위: SL -> TP -> 보유시간 초과 -> time exit).

    trailing 없음 - Panic Fade는 반등 목표가가 명확하므로 단순 TP/SL 구조.

    Args:
        buy_price: 진입가
        current_price: 현재가
        take_profit_percent: 익절 기준 (0.10 = +10%)
        stop_loss_percent: 손절 기준 (-0.10 = -10%)
        holding_hours: 보유 시간 (None이면 보유시간 체크 생략)
        max_holding_hours: 최대 보유 시간 (초과 시 반등 실패로 청산)
        hours_left: 해결까지 남은 시간 (None이면 time exit 생략)
        exit_hours: 해결 이 시간 전 청산

    Returns:
        (청산 여부, exit_reason)
    """
    pnl_percent = 0.0
    if buy_price > 0:
        pnl_percent = (current_price - buy_price) / buy_price

    # 1. 손절
    if pnl_percent <= stop_loss_percent:
        return True, "stop_loss"

    # 2. 익절 (목표가 0.99 캡 - 고가 진입 시 도달 불가 문제 수정)
    target_price = min(buy_price * (1 + take_profit_percent), TAKE_PROFIT_PRICE_CAP)
    if current_price >= target_price:
        return True, "take_profit"

    # 3. 최대 보유 시간 초과 (반등 실패)
    if holding_hours is not None and holding_hours >= max_holding_hours:
        return True, "max_holding"

    # 4. 시간 기반 청산 (해결 임박)
    if hours_left is not None and hours_left < exit_hours:
        return True, "time_exit"

    return False, "hold"
