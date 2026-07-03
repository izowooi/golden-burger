"""Conviction Ladder 전략 시그널 - 순수 함수 모음.

스냅샷 리스트/숫자를 입력받아 진입/청산 판정만 반환한다.
DB·API 호출은 scanner/trader가 담당하고, 여기는 전략 로직만 둔다.
전략을 바꾸려면 이 파일만 수정하면 된다.

방향 표기: 스냅샷의 probability는 항상 YES 토큰 가격이다.
favorite이 NO(token_index=1)면 favorite 가격 변화 = -(YES 변화).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

# 익절 목표가 상한: buy*(1+tp)가 이 값을 넘으면 여기서 익절 (도달 불가 목표가 방지)
TAKE_PROFIT_PRICE_CAP = 0.99

# 윈도우 유효성 기본값 (§3.2 시간 검증된 스냅샷 윈도우)
DEFAULT_MIN_POINTS = 5
DEFAULT_MIN_COVERAGE = 0.5

# (max_hours, band_min, band_max) - 잔여 시간 오름차순
Rung = Tuple[float, float, float]


@dataclass(frozen=True)
class Snap:
    """합성/DB/백필 공용 스냅샷 포인트. probability는 YES 가격 기준."""
    timestamp: datetime  # naive UTC
    probability: float


@dataclass(frozen=True)
class EntryDecision:
    """진입 판정 결과."""
    entry: bool
    reason: str
    hours_left: Optional[float] = None
    momentum_change: Optional[float] = None
    ladder_band: Optional[int] = None  # 진입 밴드 번호 1/2/3 (DB 회고 기록용)


# ---------------------------------------------------------------------------
# 스냅샷 윈도우 (timestamp 기반 - banana의 개수 기반 윈도우 버그 수정)
# ---------------------------------------------------------------------------

def get_window(
    snapshots: Sequence,
    hours_back: float,
    now: Optional[datetime] = None,
) -> List:
    """now 기준 최근 hours_back 시간 내 스냅샷만 추출 (시간 오름차순 정렬).

    Args:
        snapshots: .timestamp(naive UTC)/.probability 속성을 가진 객체 리스트
        hours_back: 윈도우 길이 (시간)
        now: 기준 시각 (기본: utcnow)

    Returns:
        timestamp >= now - hours_back 인 스냅샷 리스트 (오래된 것 먼저)
    """
    now = now or datetime.utcnow()
    cutoff = now - timedelta(hours=hours_back)
    window = [s for s in snapshots if s.timestamp >= cutoff]
    return sorted(window, key=lambda s: s.timestamp)


def is_window_valid(
    window: Sequence,
    hours_back: float,
    min_points: int = DEFAULT_MIN_POINTS,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
) -> bool:
    """윈도우가 시그널 계산에 충분한지 검증.

    조건: 포인트 수 >= min_points AND
          (최신 ts - 최고령 ts) >= min_coverage * hours_back

    Jenkins 중단으로 윈도우가 몇 시간을 건너뛰어도 개수만 채워지는
    banana의 버그를 막는다. invalid면 진입하지 않는다 (관대한 폴백 금지).

    Args:
        window: get_window()가 반환한 시간 오름차순 스냅샷 리스트
        hours_back: 윈도우 길이 (시간)
        min_points: 최소 포인트 수
        min_coverage: 최소 시간 커버리지 비율 (0.5 = 윈도우의 절반)
    """
    if len(window) < min_points:
        return False
    coverage_hours = (window[-1].timestamp - window[0].timestamp).total_seconds() / 3600
    return coverage_hours >= min_coverage * hours_back


def merge_snapshots(primary: Sequence, secondary: Sequence) -> List:
    """두 스냅샷 리스트 병합 (분 단위 중복 시각 제거, primary 우선).

    DB 스냅샷(primary)과 prices-history 백필(secondary)을 합칠 때 사용.

    Returns:
        시간 오름차순 병합 리스트
    """
    def minute_key(snap) -> int:
        return int(snap.timestamp.timestamp() // 60)

    merged = {}
    for snap in secondary:
        merged[minute_key(snap)] = snap
    for snap in primary:
        merged[minute_key(snap)] = snap
    return sorted(merged.values(), key=lambda s: s.timestamp)


# ---------------------------------------------------------------------------
# 시간 사다리 (Conviction Ladder)
# ---------------------------------------------------------------------------

def ladder_band(
    hours_left: Optional[float],
    entry_hours_min: float,
    rungs: Sequence[Rung],
) -> Optional[Tuple[int, float, float]]:
    """잔여 시간에 해당하는 사다리 밴드 반환.

    Args:
        hours_left: 해결까지 남은 시간
        entry_hours_min: 이 시간 이하 잔여는 진입 금지
        rungs: (max_hours, band_min, band_max) 리스트 (오름차순)

    Returns:
        (band_no(1부터), band_min, band_max) 또는 시간 창 밖이면 None
    """
    if hours_left is None or hours_left <= entry_hours_min:
        return None
    for band_no, (max_hours, band_min, band_max) in enumerate(rungs, start=1):
        if hours_left <= max_hours:
            return (band_no, band_min, band_max)
    return None


def check_ladder_entry(
    price: float,
    hours_left: Optional[float],
    entry_hours_min: float,
    rungs: Sequence[Rung],
) -> Tuple[bool, str]:
    """시간 사다리 진입 조건 검사.

    시간이 많이 남을수록 불확실성이 크므로 낮은 밴드(싼 가격)만 허용한다.
    cherry의 "잔여 시간 무관 고정 확률 밴드" 허점(①) 수정.

    Returns:
        (진입 가능 여부, 사유 문자열)
    """
    if hours_left is None:
        return False, "no_end_date"
    if hours_left <= 0:
        return False, "already_resolved"
    if hours_left <= entry_hours_min:
        return False, f"too_late_{hours_left:.1f}h"

    band = ladder_band(hours_left, entry_hours_min, rungs)
    if band is None:
        return False, f"too_early_{hours_left:.1f}h"

    band_no, band_min, band_max = band
    if not (band_min <= price <= band_max):
        return False, f"price_out_of_band{band_no}_{price:.2f}"

    return True, f"ladder{band_no}_{hours_left:.1f}h"


# ---------------------------------------------------------------------------
# 모멘텀 게이트 (떨어지는 칼날 배제 - cherry 허점 ③ 수정)
# ---------------------------------------------------------------------------

def favorite_price_change(window: Sequence, favorite_index: int) -> Optional[float]:
    """윈도우 내 favorite 가격 변화 (최신 - 최고령).

    스냅샷은 YES 가격 기준이므로 favorite이 NO(index=1)면 부호를 뒤집는다.

    Args:
        window: 시간 오름차순 스냅샷 리스트
        favorite_index: 0(YES) 또는 1(NO)

    Returns:
        favorite 가격 변화, 포인트가 2개 미만이면 None
    """
    if len(window) < 2:
        return None
    yes_change = window[-1].probability - window[0].probability
    return yes_change if favorite_index == 0 else -yes_change


def check_momentum_gate(
    window: Sequence,
    favorite_index: int,
    min_change: float,
) -> Tuple[bool, str, Optional[float]]:
    """모멘텀 게이트: favorite 가격이 하락 추세면 진입 배제.

    Returns:
        (통과 여부, 사유, favorite 가격 변화)
    """
    change = favorite_price_change(window, favorite_index)
    if change is None:
        return False, "insufficient_momentum_data", None
    if change < min_change:
        return False, f"momentum_down_{change:+.3f}", change
    return True, f"mom{change:+.3f}", change


# ---------------------------------------------------------------------------
# 종합 진입 판정
# ---------------------------------------------------------------------------

def evaluate_entry(
    price: float,
    hours_left: Optional[float],
    snapshots: Sequence,
    favorite_index: int,
    *,
    entry_hours_min: float,
    rungs: Sequence[Rung],
    momentum_lookback_hours: float,
    momentum_min_change: float,
    now: Optional[datetime] = None,
    min_points: int = DEFAULT_MIN_POINTS,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
) -> EntryDecision:
    """Conviction Ladder 진입 판정 (순수 함수).

    검사 순서:
    1. 시간 사다리 밴드 (잔여 시간 구간별 확률 밴드)
    2. 스냅샷 윈도우 유효성 (invalid면 진입 금지)
    3. 모멘텀 게이트 (favorite 가격 변화 >= min_change)

    유동성/거래량/재진입 쿨다운은 scanner/trader가 별도 검사한다.

    Args:
        price: favorite 토큰 현재 가격
        hours_left: 해결까지 남은 시간
        snapshots: YES 가격 기준 스냅샷 리스트 (백필 병합 완료 상태)
        favorite_index: 0(YES) 또는 1(NO)
    """
    ladder_ok, ladder_reason = check_ladder_entry(price, hours_left, entry_hours_min, rungs)
    if not ladder_ok:
        return EntryDecision(False, ladder_reason, hours_left)

    window = get_window(snapshots, momentum_lookback_hours, now)
    if not is_window_valid(window, momentum_lookback_hours, min_points, min_coverage):
        return EntryDecision(False, "window_invalid", hours_left)

    gate_ok, gate_reason, change = check_momentum_gate(window, favorite_index, momentum_min_change)
    if not gate_ok:
        return EntryDecision(False, gate_reason, hours_left, change)

    # 판정에는 쓰지 않는 회고 기록용 수치 (entry_reason 문자열 파싱 없이 컬럼으로 적재)
    band = ladder_band(hours_left, entry_hours_min, rungs)
    band_no = band[0] if band else None

    return EntryDecision(True, f"{ladder_reason}_{gate_reason}", hours_left, change, band_no)


# ---------------------------------------------------------------------------
# 청산 판정
# ---------------------------------------------------------------------------

def take_profit_target(buy_price: float, take_profit_percent: float) -> float:
    """익절 목표가. buy*(1+tp)가 0.99를 넘으면 0.99로 캡 (§3.5 도달 불가 수정)."""
    return min(buy_price * (1 + take_profit_percent), TAKE_PROFIT_PRICE_CAP)


def evaluate_exit(
    buy_price: float,
    current_price: float,
    max_price: float,
    hours_left: Optional[float],
    *,
    stop_loss_percent: float,
    take_profit_percent: float,
    trailing_enabled: bool,
    trailing_percent: float,
    exit_hours: float,
) -> Optional[str]:
    """청산 판정 (우선순위 순).

    1. stop_loss     : P&L <= stop_loss_percent
    2. take_profit   : 현재가 >= 익절 목표가 (0.99 캡)
    3. trailing_stop : 현재가 < 최고가 * (1 - trailing_percent)
    4. time_exit     : 해결까지 < exit_hours (마지막 수렴 구간은 2h 전까지 보유)

    Returns:
        exit_reason 문자열 또는 청산 조건 미충족 시 None
    """
    pnl_percent = (current_price - buy_price) / buy_price if buy_price > 0 else 0.0

    if pnl_percent <= stop_loss_percent:
        return "stop_loss"

    if current_price >= take_profit_target(buy_price, take_profit_percent):
        return "take_profit"

    if trailing_enabled and max_price and max_price > 0:
        if current_price < max_price * (1 - trailing_percent):
            return "trailing_stop"

    if hours_left is not None and hours_left < exit_hours:
        return "time_exit"

    return None
