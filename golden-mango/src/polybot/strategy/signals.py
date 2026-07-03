"""Patience Premium 전략 시그널 - 순수 함수 모음.

스냅샷 리스트/숫자를 입력받아 진입/청산 판정만 반환한다.
DB·API 호출은 scanner/trader가 담당하고, 여기는 전략 로직만 둔다.
전략을 바꾸려면 이 파일만 수정하면 된다.

핵심 수식 (골든크로스급 간결함):

    연환산 캐리 수익률 y = ((1 - p) / p) * (8760 / hours_left)
    진입 <=> y >= y_min

"거의 확실한" 계약도 자본이 잠기는 기간만큼 할인되어 거래된다(settlement
discount). 그 할인 = 캐리를 연환산해 허들(y_min)을 넘는 시장만 매수한다.

방향 표기: 스냅샷의 probability는 항상 YES 토큰 가격이다.
favorite이 NO(token_index=1)면 favorite 가격 변화 = -(YES 변화).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

# float 경계 비교 오차 허용치 (0.985 같은 값이 2진수로 정확히 표현되지 않음)
EPSILON = 1e-9

# 연 환산 시간 (365일 * 24h)
HOURS_PER_YEAR = 8760.0

# 익절 목표가 상한: buy*(1+tp)가 이 값을 넘으면 여기서 익절.
# mango는 tp 기본값이 9.99라 목표가가 사실상 항상 0.99로 고정된다 (수렴 보유).
TAKE_PROFIT_PRICE_CAP = 0.99

# 윈도우 유효성 기본값 (§3.2 시간 검증된 스냅샷 윈도우)
DEFAULT_MIN_POINTS = 5
DEFAULT_MIN_COVERAGE = 0.5


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
    carry_yield: Optional[float] = None
    momentum_change: Optional[float] = None


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
    """
    if len(window) < min_points:
        return False
    coverage_hours = (window[-1].timestamp - window[0].timestamp).total_seconds() / 3600
    return coverage_hours >= min_coverage * hours_back - EPSILON


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
# 캐리 수익률 (Patience Premium 핵심 수식)
# ---------------------------------------------------------------------------

def carry_yield(price: float, hours_left: Optional[float]) -> Optional[float]:
    """연환산 캐리 수익률 y = ((1 - p) / p) * (8760 / hours_left).

    p에 사서 만기에 1.00을 받는 캐리 (1-p)/p 를 잔여 시간으로 연환산한다.
    예: p=0.95, 24h 남음 -> (0.05/0.95) * 365 = 약 19.2 (연 1921%).

    Args:
        price: 매수할 토큰의 현재 가격 (0 < p < 1)
        hours_left: 해결까지 남은 시간

    Returns:
        연환산 수익률 (2.0 = 연 200%). price/hours가 유효하지 않으면 None.
    """
    if hours_left is None or hours_left <= 0:
        return None
    if price <= 0 or price >= 1:
        return None
    return ((1.0 - price) / price) * (HOURS_PER_YEAR / hours_left)


def check_carry_entry(
    price: float,
    hours_left: Optional[float],
    *,
    yield_min: float,
    prob_min: float,
    prob_max: float,
    entry_hours_min: float,
    entry_hours_max: float,
) -> Tuple[bool, str, Optional[float]]:
    """캐리 진입 조건 검사 (시간 창 + 확률 밴드 + 수익률 허들).

    조건:
    1. entry_hours_min < hours_left <= entry_hours_max
    2. prob_min <= price <= prob_max
    3. carry_yield >= yield_min

    Returns:
        (진입 가능 여부, 사유 문자열, 계산된 carry_yield)
    """
    if hours_left is None:
        return False, "no_end_date", None
    if hours_left <= 0:
        return False, "already_resolved", None
    if hours_left <= entry_hours_min + EPSILON:
        return False, f"too_late_{hours_left:.1f}h", None
    if hours_left > entry_hours_max + EPSILON:
        return False, f"too_early_{hours_left:.1f}h", None

    if not (prob_min - EPSILON <= price <= prob_max + EPSILON):
        return False, f"price_out_of_band_{price:.3f}", None

    y = carry_yield(price, hours_left)
    if y is None:
        return False, f"invalid_price_{price:.3f}", None
    if y < yield_min - EPSILON:
        return False, f"yield_below_min_{y:.2f}", y

    return True, f"carry_y{y:.2f}_{hours_left:.1f}h", y


# ---------------------------------------------------------------------------
# 모멘텀 가드 (급락 중 진입 금지)
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
    """모멘텀 가드: favorite 가격이 급락 중이면 진입 배제.

    캐리 수확은 "가격이 그대로 만기에 수렴"이 전제다. 최근 급락은
    새 정보(오라클 분쟁·반전 뉴스)의 신호일 수 있으므로 사지 않는다.

    Returns:
        (통과 여부, 사유, favorite 가격 변화)
    """
    change = favorite_price_change(window, favorite_index)
    if change is None:
        return False, "insufficient_momentum_data", None
    if change < min_change - EPSILON:
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
    yield_min: float,
    prob_min: float,
    prob_max: float,
    entry_hours_min: float,
    entry_hours_max: float,
    momentum_lookback_hours: float,
    momentum_min_change: float,
    now: Optional[datetime] = None,
    min_points: int = DEFAULT_MIN_POINTS,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
) -> EntryDecision:
    """Patience Premium 진입 판정 (순수 함수).

    검사 순서:
    1. 캐리 진입 조건 (시간 창 + 확률 밴드 + 수익률 허들 y >= y_min)
    2. 스냅샷 윈도우 유효성 (invalid면 진입 금지 - 백필은 scanner가 선행)
    3. 모멘텀 가드 (favorite 최근 변화 >= min_change)

    유동성/재진입 쿨다운은 scanner/trader가 별도 검사한다.

    Args:
        price: favorite 토큰 현재 가격
        hours_left: 해결까지 남은 시간
        snapshots: YES 가격 기준 스냅샷 리스트 (백필 병합 완료 상태)
        favorite_index: 0(YES) 또는 1(NO)
    """
    carry_ok, carry_reason, y = check_carry_entry(
        price,
        hours_left,
        yield_min=yield_min,
        prob_min=prob_min,
        prob_max=prob_max,
        entry_hours_min=entry_hours_min,
        entry_hours_max=entry_hours_max,
    )
    if not carry_ok:
        return EntryDecision(False, carry_reason, hours_left, y)

    window = get_window(snapshots, momentum_lookback_hours, now)
    if not is_window_valid(window, momentum_lookback_hours, min_points, min_coverage):
        return EntryDecision(False, "window_invalid", hours_left, y)

    gate_ok, gate_reason, change = check_momentum_gate(
        window, favorite_index, momentum_min_change
    )
    if not gate_ok:
        return EntryDecision(False, gate_reason, hours_left, y, change)

    return EntryDecision(True, f"{carry_reason}_{gate_reason}", hours_left, y, change)


# ---------------------------------------------------------------------------
# 청산 판정
# ---------------------------------------------------------------------------

def take_profit_target(buy_price: float, take_profit_percent: float) -> float:
    """익절 목표가. buy*(1+tp)가 0.99를 넘으면 0.99로 캡 (§3.5).

    mango는 take_profit_percent 기본값이 9.99이므로 목표가가 사실상 항상
    0.99다 - "0.99 도달 = 수렴 완료"가 이 전략의 익절이다.
    """
    return min(buy_price * (1 + take_profit_percent), TAKE_PROFIT_PRICE_CAP)


def evaluate_exit(
    buy_price: float,
    current_price: float,
    hours_left: Optional[float],
    *,
    stop_loss_percent: float,
    take_profit_percent: float,
    exit_hours: float,
) -> Optional[str]:
    """청산 판정 (우선순위 순).

    1. stop_loss   : P&L <= stop_loss_percent (기본 -6%; 수렴 실패 신호)
    2. take_profit : 현재가 >= 목표가 (사실상 0.99 도달 = 수렴 완료)
    3. time_exit   : 해결까지 < exit_hours (기본 2h; 마지막까지 캐리 수확)

    trailing stop 없음 - 수렴 보유가 본질이므로 조기 익절을 하지 않는다.

    Returns:
        exit_reason 문자열 또는 청산 조건 미충족 시 None
    """
    pnl_percent = (current_price - buy_price) / buy_price if buy_price > 0 else 0.0

    if pnl_percent <= stop_loss_percent + EPSILON:
        return "stop_loss"

    if current_price >= take_profit_target(buy_price, take_profit_percent) - EPSILON:
        return "take_profit"

    if hours_left is not None and hours_left < exit_hours:
        return "time_exit"

    return None
