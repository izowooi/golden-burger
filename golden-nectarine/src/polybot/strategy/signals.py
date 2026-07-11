"""Bottom Fisher 전략 시그널 - 순수 함수 모음.

이 모듈은 DB/API에 의존하지 않는다. 스냅샷 리스트와 숫자만 입력받아
진입/청산 판정을 출력한다. scanner/trader가 이 함수들을 호출하며,
전략을 바꾸려면 이 파일만 수정하면 된다.

전략 요약 (QuantPedia 2026-04 X=20/Y=5 규칙의 시간별 가격 근사):
- 진입 ⇔ p_now <= min(지난 20일 롤링 윈도우, 단 최근 24h 제외 구간의 최저가)
- 청산 ⇔ 보유 120시간(5일) 경과 (calendar exit = 주 청산 경로)
- YES 토큰 매수 고정, p ∈ [0.03, 0.50] tail~중간 구간

스냅샷 히스토리는 항상 YES 가격 기준으로 저장된다. 이 전략은 YES 매수
고정이므로 1-p 환산이 필요 없다. 다만 원문의 daily-close 계열을 직접
재현하지 않고 CLOB fidelity=60 가격으로 근사한다.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, NamedTuple, Optional

# 부동소수점 경계 비교 오차 허용치 (0.30-0.27=0.0299... 같은 표현 오차 흡수)
EPSILON = 1e-9

# take_profit 목표가 캡 - buy_price*(1+tp)가 0.99를 넘으면 0.99 도달 시 익절
TAKE_PROFIT_PRICE_CAP = 0.99

# CLOB prices-history는 fidelity=60(시간 단위)로 받아 daily close를 정확히
# 재현할 수 없다. 대신 20일의 95% 시간 span과 최소 일별 1점 수준의 밀도를
# 요구해 10일짜리 부분 윈도우를 20일 신저가로 오인하지 않는다.
HOURLY_APPROX_MIN_POINTS = 20
HOURLY_APPROX_MIN_COVERAGE = 0.95


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
    "20일 윈도우"가 실제 20일을 벗어나지 않는다.

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
    min_points: int = HOURLY_APPROX_MIN_POINTS,
    min_coverage: float = HOURLY_APPROX_MIN_COVERAGE,
) -> bool:
    """윈도우가 신호 계산에 충분한 데이터를 갖는지 검증.

    조건: 포인트 수 >= min_points AND
          (최신 ts - 최고(最古) ts) >= min_coverage * hours_back

    invalid면 진입하지 않는다 (banana의 관대한 cold-start 폴백 금지).
    20일 룩백은 최소 19일(95%) span과 20개 포인트를 요구한다. 이는
    daily-close 직접 복제가 아닌 hourly 가격 근사의 fail-closed 기준이다.
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
# Bottom Fisher 진입 시그널
# ---------------------------------------------------------------------------

@dataclass
class BottomFisherParams:
    """Bottom Fisher 진입 파라미터 (config에서 주입)."""
    lookback_days: float = 20.0         # 롤링 최저가 룩백 (일)
    exclude_recent_hours: float = 24.0  # 최저가 산출 시 제외할 최근 구간 (시간)
    prob_min: float = 0.03              # 진입 가능한 YES 가격 하한
    prob_max: float = 0.50              # 진입 가능한 YES 가격 상한
    window_min_points: int = HOURLY_APPROX_MIN_POINTS
    window_min_coverage: float = HOURLY_APPROX_MIN_COVERAGE


@dataclass
class BottomFisherSignal:
    """진입 판정 결과. YES 매수 고정이므로 side/token_index는 항상 Yes/0."""
    entry: bool
    reason: str
    rolling_min: Optional[float] = None          # 룩백(최근 24h 제외) 최저가
    lookback_days_covered: Optional[float] = None  # 윈도우 실제 커버 일수
    current_price: Optional[float] = None        # 현재 YES 가격


def evaluate_bottom_fisher(
    yes_snapshots: List[PricePoint],
    yes_price_now: float,
    params: BottomFisherParams,
    now: Optional[datetime] = None,
) -> BottomFisherSignal:
    """Bottom Fisher 진입 시그널 판정 (순수 함수).

    진입 조건 (모두 충족, YES 토큰 기준 가격 p):
    1. 윈도우 유효성 통과 (시간별 근사, 20일 룩백의 95% - 백필 포함)
    2. p_now ∈ [0.03, 0.50] (tail~중간 구간)
    3. 기준 최저가 = 룩백 윈도우 중 최근 24h를 제외한 구간의 min
    4. p_now <= 기준 최저가 (동률 허용 <=, EPSILON 오차 흡수)

    최근 24h 제외 이유: 현재 진행 중인 하락 자체를 기준선에 포함하면
    "신저가" 판정이 자기 자신과의 비교가 되어 무의미해진다. 백테스트의
    X일 롤링 최저가 돌파(신저가 매수) 정의를 보존하기 위한 장치다.

    Args:
        yes_snapshots: YES 가격 기준 스냅샷 시계열 (DB + 백필 병합)
        yes_price_now: 현재 YES 가격
        params: 전략 파라미터
        now: 기준 시각 (기본: utcnow)

    Returns:
        BottomFisherSignal (entry=True면 YES 토큰 매수)
    """
    if now is None:
        now = datetime.utcnow()

    hours_back = params.lookback_days * 24.0

    # 1. 윈도우 유효성 (timestamp 기반)
    window = get_window(yes_snapshots, hours_back, now)
    if not is_window_valid(
        window,
        hours_back,
        min_points=params.window_min_points,
        min_coverage=params.window_min_coverage,
    ):
        return BottomFisherSignal(
            entry=False, reason="window_invalid", current_price=yes_price_now
        )

    lookback_days_covered = (
        (window[-1].timestamp - window[0].timestamp).total_seconds() / 86400.0
    )

    # 2. 가격 밴드 (tail~중간 구간, EPSILON 경계 오차 흡수)
    if not (params.prob_min - EPSILON <= yes_price_now <= params.prob_max + EPSILON):
        return BottomFisherSignal(
            entry=False,
            reason=f"price_out_of_band_{yes_price_now:.3f}",
            lookback_days_covered=lookback_days_covered,
            current_price=yes_price_now,
        )

    # 3. 기준 최저가 = 최근 exclude_recent_hours 제외 구간의 min
    ref_cutoff = now - timedelta(hours=params.exclude_recent_hours)
    ref_points = [p for p in window if p.timestamp <= ref_cutoff]
    if not ref_points:
        return BottomFisherSignal(
            entry=False, reason="no_ref_data",
            lookback_days_covered=lookback_days_covered,
            current_price=yes_price_now,
        )

    rolling_min = min(p.price for p in ref_points)

    # 4. 신저가 판정 (동률 허용 <=)
    if yes_price_now > rolling_min + EPSILON:
        return BottomFisherSignal(
            entry=False,
            reason=f"above_rolling_min_{rolling_min:.3f}",
            rolling_min=rolling_min,
            lookback_days_covered=lookback_days_covered,
            current_price=yes_price_now,
        )

    return BottomFisherSignal(
        entry=True,
        reason=f"bottom_fisher_min{rolling_min:.3f}_p{yes_price_now:.3f}",
        rolling_min=rolling_min,
        lookback_days_covered=lookback_days_covered,
        current_price=yes_price_now,
    )


# ---------------------------------------------------------------------------
# 청산 시그널
# ---------------------------------------------------------------------------

def capped_take_profit_target(
    buy_price: float,
    take_profit_percent: float,
    cap: float = TAKE_PROFIT_PRICE_CAP,
) -> float:
    """익절 목표가 계산: buy*(1+tp)가 0.99를 넘으면 0.99로 캡.

    캡이 없으면 고가 진입 건은 목표가가 1.0을 넘어 영구 도달 불가가 된다
    (cherry의 확인된 버그 수정 - 이 전략은 진입가 <= 0.50이라 실질 영향은
    없지만 전 봇 공통 규칙으로 유지).
    """
    return min(buy_price * (1.0 + take_profit_percent), cap)


def evaluate_exit(
    buy_price: float,
    current_price: float,
    take_profit_percent: float,
    stop_loss_percent: float,
    holding_hours: Optional[float],
    hold_hours: float,
    hours_left: Optional[float],
    exit_hours: float,
) -> tuple[bool, str]:
    """청산 판정 (우선순위: calendar exit -> SL -> TP -> time exit).

    **calendar exit(max_holding)가 주 청산 경로다** - QuantPedia 백테스트의
    "Y=5일 후 무조건 청산" 규칙 복제가 이 전략의 본질이므로, 보유 120h
    도달 시 손익과 무관하게 가장 먼저 청산한다. SL/TP는 ±30%의 넉넉한
    안전판으로만 작동한다. trailing 없음.

    Args:
        buy_price: 진입가
        current_price: 현재가
        take_profit_percent: 익절 안전판 (0.30 = +30%)
        stop_loss_percent: 손절 안전판 (-0.30 = -30%)
        holding_hours: 보유 시간 (None이면 calendar exit 생략)
        hold_hours: calendar exit 보유 시간 (기본 120h = 5일)
        hours_left: 해결까지 남은 시간 (None이면 time exit 생략)
        exit_hours: 해결 이 시간 전 청산

    Returns:
        (청산 여부, exit_reason)
    """
    # 1. calendar exit - 백테스트 복제의 핵심, 무조건 최우선
    if holding_hours is not None and holding_hours >= hold_hours:
        return True, "max_holding"

    pnl_percent = 0.0
    if buy_price > 0:
        pnl_percent = (current_price - buy_price) / buy_price

    # 2. 손절 안전판
    if pnl_percent <= stop_loss_percent:
        return True, "stop_loss"

    # 3. 익절 안전판 (조기 행운 익절, 목표가 0.99 캡)
    if current_price >= capped_take_profit_target(buy_price, take_profit_percent):
        return True, "take_profit"

    # 4. 시간 기반 청산 (해결 임박 - hours_left >= 720 진입이라 드문 경로)
    if hours_left is not None and hours_left < exit_hours:
        return True, "time_exit"

    return False, "hold"
