"""Fear Spike Fade 전략 시그널 - 순수 함수 모음.

이 모듈의 함수는 전부 순수 함수다: 스냅샷 리스트/숫자 입력 → 판정 출력.
API, DB, 설정 파일에 의존하지 않으므로 유닛테스트가 그대로 전략 검증이 된다.
scanner/trader는 여기서 판정만 받아 실행한다. 전략 변경 = 이 파일 수정.

전략 요약 (probability neglect + availability cascade 페이드):
- 평시 YES <= 15%인 tail 시장이 공포 헤드라인에 +0.10 이상 급등(스파이크)하면,
  대중은 확률이 아니라 결과의 끔찍함에 반응해 YES를 '보험/복권'으로 과매수한다.
- 스파이크 시작 90분 경과 + 최근 45분 신고가 없음(스톨) + 거래량 2배 확인 후
  NO 토큰을 매수해 공포 프리미엄의 감쇠(YES 되돌림)를 수확한다.
- 주 청산은 retrace_target: YES <= base + retrace_ratio*(peak - base).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence

# 익절 목표가 상한: buy_price*(1+tp)가 0.99를 넘으면 0.99 도달 시 익절
TP_PRICE_CAP = 0.99

# 윈도우 유효성 기본값 (§3.2 timestamp 기반 윈도우)
WINDOW_MIN_POINTS = 5
WINDOW_MIN_COVERAGE = 0.5

# float 뺄셈 오차 보정 (예: 0.25 - 0.15 = 0.0999...가 0.10 경계를 놓치는 문제)
EPSILON = 1e-9


@dataclass(frozen=True)
class PricePoint:
    """스냅샷 1개: YES 가격 기준. DB MarketSnapshot과 attribute 호환."""
    timestamp: datetime            # naive UTC
    probability: float             # YES 가격 (0.0~1.0)
    volume_24h: Optional[float] = None  # gamma volume24hr (백필 포인트는 None)


@dataclass(frozen=True)
class SignalParams:
    """진입 판정 파라미터 (config에서 주입)."""
    base_window_days: float = 7.0
    base_exclude_recent_hours: float = 6.0
    base_max: float = 0.15
    jump_min: float = 0.10
    yes_max: float = 0.30
    spike_wait_minutes: float = 90.0
    stall_window_minutes: float = 45.0
    vol_mult_min: float = 2.0
    entry_hours_min: float = 72.0
    min_liquidity: float = 15000.0
    min_volume_24h: float = 0.0


@dataclass(frozen=True)
class EntrySignal:
    """진입 판정 결과. 수치 시그널은 DB *_at_buy 컬럼으로 기록된다 (부록 §A)."""
    entry: bool
    reason: str
    base_price: Optional[float] = None          # base (7d 중앙값)
    spike_peak: Optional[float] = None          # 진입 시점까지의 스파이크 고점
    spike_age_minutes: Optional[float] = None   # 스파이크 시작 후 경과 분
    vol_mult: Optional[float] = None            # 현재 volume24h / 윈도우 평균


@dataclass(frozen=True)
class ExitSignal:
    """청산 판정 결과."""
    should_sell: bool
    reason: str  # "stop_loss" | "retrace_target" | "take_profit" | "max_holding" | "time_exit" | "hold"


def to_price_points(snapshots: Sequence) -> List[PricePoint]:
    """DB MarketSnapshot 리스트 → PricePoint 리스트 (YES 가격 기준)."""
    return [
        PricePoint(
            timestamp=s.timestamp,
            probability=float(s.probability),
            volume_24h=float(s.volume_24h) if getattr(s, "volume_24h", None) is not None else None,
        )
        for s in snapshots
    ]


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


def merge_price_points(db_points: Sequence, backfill_points: Sequence) -> List[PricePoint]:
    """DB 스냅샷과 히스토리 백필 포인트 병합.

    분 단위로 반올림한 timestamp가 같으면 중복으로 보고 DB 스냅샷을 우선한다
    (DB 스냅샷만 volume_24h를 갖는다).

    Returns:
        timestamp 오름차순 병합 PricePoint 리스트
    """
    merged = {}
    # 백필 먼저 넣고 DB로 덮어써서 DB 우선
    for point in list(backfill_points) + list(db_points):
        key = point.timestamp.replace(second=0, microsecond=0)
        merged[key] = PricePoint(
            timestamp=point.timestamp,
            probability=float(point.probability),
            volume_24h=getattr(point, "volume_24h", None),
        )
    return sorted(merged.values(), key=lambda p: p.timestamp)


def compute_base(
    window: Sequence,
    now: Optional[datetime] = None,
    exclude_recent_hours: float = 6.0,
) -> Optional[float]:
    """base = median(YES, [now-window, now-exclude_recent_hours]).

    스파이크 자체가 base를 끌어올리지 않도록 최근 exclude_recent_hours를
    제외한 구간의 중앙값을 쓴다. 해당 구간에 포인트가 없으면 None.
    """
    if now is None:
        now = datetime.utcnow()
    cutoff = now - timedelta(hours=exclude_recent_hours)
    prices = sorted(p.probability for p in window if p.timestamp <= cutoff)
    if not prices:
        return None
    mid = len(prices) // 2
    if len(prices) % 2 == 1:
        return prices[mid]
    return (prices[mid - 1] + prices[mid]) / 2.0


def find_spike_start(
    window: Sequence,
    threshold: float,
) -> Optional[datetime]:
    """스파이크 시작 시각 = 스냅샷에서 처음 yes >= threshold(base+jump_min)를 넘은 시각.

    윈도우 내에 threshold를 넘은 스냅샷이 없으면 None
    (= 스파이크가 방금 시작됐거나 현재가만 넘은 상태 → 경과 시간 0으로 취급).
    """
    for point in sorted(window, key=lambda p: p.timestamp):
        if point.probability >= threshold - EPSILON:
            return point.timestamp
    return None


def spike_peak_price(
    window: Sequence,
    spike_start: datetime,
    current_price: float,
) -> float:
    """진입 시점까지의 스파이크 고점 (spike_start 이후 최고 YES, 현재가 포함)."""
    prices = [p.probability for p in window if p.timestamp >= spike_start]
    prices.append(current_price)
    return max(prices)


def is_spike_stalled(
    window: Sequence,
    current_price: float,
    stall_window_minutes: float,
    now: Optional[datetime] = None,
) -> bool:
    """스파이크 스톨 확인: 최근 stall_window 분에 YES 신고가가 없어야 True.

    최근 구간의 최고가(현재가 포함)가 그 이전 구간의 고점을 넘으면
    스파이크가 아직 진행 중이다 → 페이드 진입 금지 (떨어지는 칼날 반대편).
    이전 구간 데이터가 없으면 판단 불가 → 보수적으로 False.
    """
    if now is None:
        now = datetime.utcnow()
    cutoff = now - timedelta(minutes=stall_window_minutes)
    before = [p.probability for p in window if p.timestamp < cutoff]
    if not before:
        return False
    prior_peak = max(before)
    recent = [p.probability for p in window if p.timestamp >= cutoff]
    recent_peak = max(recent + [current_price])
    return recent_peak <= prior_peak + EPSILON


def volume_multiple(
    window: Sequence,
    current_volume_24h: Optional[float],
) -> Optional[float]:
    """현재 volume24hr / 윈도우 평균 volume24hr.

    백필 포인트는 volume이 없으므로(None) 실제 축적된 스냅샷이 있어야
    계산할 수 있다 (의도된 보수성 - lime과 동일). 계산 불가 시 None.
    """
    vols = [
        float(p.volume_24h) for p in window
        if getattr(p, "volume_24h", None) is not None and float(p.volume_24h) > 0
    ]
    if not vols or not current_volume_24h or current_volume_24h <= 0:
        return None
    return current_volume_24h / (sum(vols) / len(vols))


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
    """Fear Spike Fade 진입 판정 (모두 충족해야 진입 → NO 토큰 매수).

    1. liquidity >= min_liquidity (+ 옵션 volume_24h)
    2. hours_left >= entry_hours_min (마감 임박 스파이크 = 진짜 정보일 가능성 배제)
    3. 윈도우 유효성 (7d, 백필 병합 후)
    4. base = median(YES, [now-7d, now-6h]) <= base_max
    5. 스파이크: yes_now - base >= jump_min AND yes_now <= yes_max
    6. 스파이크 시작 후 spike_wait_minutes 경과
    7. 최근 stall_window_minutes 내 YES 신고가 없음 (스톨 확인)
    8. volume24h >= vol_mult_min x 윈도우 평균

    Args:
        yes_price: 현재 YES 가격 (매수 대상은 NO = 1 - yes_price)
        snapshots: YES 가격 기준 스냅샷 리스트 (DB + 백필 병합본)
        now: 기준 시각 (테스트 주입용)

    Returns:
        EntrySignal(entry, reason, base_price, spike_peak, spike_age_minutes, vol_mult)
    """
    if now is None:
        now = datetime.utcnow()

    # 1. 유동성 / 거래량
    if liquidity < params.min_liquidity:
        return EntrySignal(False, f"low_liquidity_{liquidity:.0f}")
    if params.min_volume_24h > 0 and volume_24h < params.min_volume_24h:
        return EntrySignal(False, f"low_volume_{volume_24h:.0f}")

    # 2. 시간 윈도우 (하한만 - 마감 임박 스파이크는 진짜 정보일 수 있다)
    if hours_left is None:
        return EntrySignal(False, "no_end_date")
    if hours_left <= 0:
        return EntrySignal(False, "already_resolved")
    if hours_left < params.entry_hours_min:
        return EntrySignal(False, f"too_close_to_resolution_{hours_left:.1f}h")

    # 3. 윈도우 유효성 - invalid면 진입하지 않는다
    lookback_hours = params.base_window_days * 24.0
    window = get_window(snapshots, lookback_hours, now=now)
    if not is_window_valid(window, lookback_hours):
        return EntrySignal(False, "window_invalid")

    # 4. base (평시 확률): 최근 6h 제외 7d 중앙값 <= 0.15
    base = compute_base(
        window, now=now, exclude_recent_hours=params.base_exclude_recent_hours
    )
    if base is None:
        return EntrySignal(False, "base_undefined")
    if base > params.base_max + EPSILON:
        return EntrySignal(False, f"base_too_high_{base:.3f}", base_price=base)

    # 5. 스파이크: yes_now - base >= jump_min AND yes_now <= yes_max
    jump = yes_price - base
    if jump < params.jump_min - EPSILON:
        return EntrySignal(False, f"no_spike_{jump:+.3f}", base_price=base)
    if yes_price > params.yes_max + EPSILON:
        # 러닝룸 없음: 이미 진짜 정보로 재평가된 수준일 수 있다
        return EntrySignal(False, f"yes_too_high_{yes_price:.3f}", base_price=base)

    # 6. 스파이크 시작 90분 경과 (감정 스파이크의 초기 과열 구간 회피)
    threshold = base + params.jump_min
    spike_start = find_spike_start(window, threshold)
    if spike_start is None:
        # 스냅샷에는 아직 threshold를 넘은 포인트가 없다 = 방금 시작
        return EntrySignal(False, "spike_too_fresh_0m", base_price=base)
    age_minutes = (now - spike_start).total_seconds() / 60.0
    if age_minutes < params.spike_wait_minutes - EPSILON:
        return EntrySignal(
            False, f"spike_too_fresh_{age_minutes:.0f}m",
            base_price=base, spike_age_minutes=age_minutes,
        )

    # 7. 스파이크 스톨: 최근 45분 신고가 없음
    if not is_spike_stalled(
        window, yes_price, params.stall_window_minutes, now=now
    ):
        return EntrySignal(
            False, "spike_still_running",
            base_price=base, spike_age_minutes=age_minutes,
        )

    # 8. 거래량 확인: 공포가 실제 거래로 이어졌는지 (조용한 노이즈 배제)
    vol_mult = volume_multiple(window, volume_24h)
    if vol_mult is None or vol_mult < params.vol_mult_min - EPSILON:
        return EntrySignal(
            False,
            f"volume_unconfirmed_{vol_mult:.2f}x" if vol_mult is not None else "volume_unconfirmed",
            base_price=base, spike_age_minutes=age_minutes, vol_mult=vol_mult,
        )

    peak = spike_peak_price(window, spike_start, yes_price)
    return EntrySignal(
        True,
        f"fear_spike_fade_base{base:.2f}_yes{yes_price:.2f}_{age_minutes:.0f}m",
        base_price=base,
        spike_peak=peak,
        spike_age_minutes=age_minutes,
        vol_mult=vol_mult,
    )


def take_profit_target(
    buy_price: float,
    take_profit_percent: float,
    cap: float = TP_PRICE_CAP,
) -> float:
    """익절 목표가. buy_price*(1+tp)가 cap(0.99)을 넘으면 cap으로 제한.

    NO를 0.93에 사면 +8% 목표가는 1.004로 도달 불가 → 0.99 도달 시 익절
    (기존 봇의 take_profit 도달 불가 버그 수정).
    """
    return min(buy_price * (1 + take_profit_percent), cap)


def retrace_target_price(
    base_price: float,
    spike_peak: float,
    retrace_ratio: float,
) -> float:
    """retrace 익절 기준 YES 가격 = base + retrace_ratio * (peak - base)."""
    return base_price + retrace_ratio * (spike_peak - base_price)


def evaluate_exit(
    *,
    buy_price: float,
    current_price: float,
    current_yes_price: Optional[float],
    base_price: Optional[float],
    spike_peak: Optional[float],
    retrace_ratio: float,
    holding_hours: Optional[float],
    hours_left: Optional[float],
    take_profit_percent: float,
    stop_loss_percent: float,
    exit_hours: float,
    max_holding_hours: float,
) -> ExitSignal:
    """Fear Spike Fade 청산 판정 (우선순위 순, trailing 없음).

    1. 손절: NO P&L <= stop_loss_percent (YES가 계속 오름 = 진짜 정보 → 즉시 손절)
    2. retrace_target (주 청산): YES <= base + retrace_ratio*(peak - base)
       - grape의 drift_death처럼 시그널 기반 청산. trade에 저장된
         base_price_at_buy/spike_peak_at_buy와 스냅샷 최신 YES를 비교한다.
       - current_yes_price가 없으면(스냅샷 미존재) 판단 보류 후 다음 조건으로.
    3. 익절(보조): NO 현재가 >= 목표가 (0.99 캡)
    4. max_holding: 보유 max_holding_hours 초과 (되돌림 실패)
    5. 시간 청산: 해결까지 exit_hours 미만

    Args:
        buy_price: NO 매수가
        current_price: NO 현재가 (midpoint)
        current_yes_price: 스냅샷 최신 YES 가격 (1-NO midpoint 근사 대신 사용)

    Returns:
        ExitSignal(should_sell, reason)
    """
    pnl_percent = 0.0
    if buy_price and buy_price > 0:
        pnl_percent = (current_price - buy_price) / buy_price

    # 1. 손절 (유일한 '진짜 정보' 방어선)
    if pnl_percent <= stop_loss_percent:
        return ExitSignal(True, "stop_loss")

    # 2. retrace_target (주 청산): 공포 프리미엄이 절반 이상 빠지면 실현
    if (
        current_yes_price is not None
        and base_price is not None
        and spike_peak is not None
        and spike_peak > base_price + EPSILON
    ):
        target_yes = retrace_target_price(base_price, spike_peak, retrace_ratio)
        if current_yes_price <= target_yes + EPSILON:
            return ExitSignal(True, "retrace_target")

    # 3. 익절 (보조, 0.99 캡)
    target = take_profit_target(buy_price, take_profit_percent)
    if current_price >= target - EPSILON:
        return ExitSignal(True, "take_profit")

    # 4. 최대 보유 시간 초과 (되돌림 실패 → 자본 회수)
    if holding_hours is not None and holding_hours >= max_holding_hours:
        return ExitSignal(True, "max_holding")

    # 5. 시간 청산
    if hours_left is not None and hours_left < exit_hours:
        return ExitSignal(True, "time_exit")

    return ExitSignal(False, "hold")
