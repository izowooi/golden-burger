"""Market scanner for finding trading opportunities (Fear Spike Fade strategy)."""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from ..api.gamma_client import GammaClient
from ..api.history_client import HistoryClient
from ..config import TradingConfig
from ..db.repository import TradeRepository
from .filters import (
    is_sports_market,
    passes_liquidity_filter,
    get_no_side,
)
from .signals import (
    SignalParams,
    evaluate_entry,
    get_window,
    is_window_valid,
    merge_price_points,
    to_price_points,
)

logger = logging.getLogger(__name__)

_NUMERIC_REASON_PART = re.compile(r"^[+-]?\d[\d.]*[a-z%]*$")


def _reason_key(reason: str) -> str:
    """제외 사유의 수치 접미사를 떼고 집계 키로 정규화.

    예: base_too_high_0.153 → base_too_high, no_spike_+0.031 → no_spike
    """
    parts = [p for p in reason.split("_") if p and not _NUMERIC_REASON_PART.match(p)]
    return "_".join(parts) or reason


def _log_reject_summary(rejected: Dict[str, int]) -> None:
    """제외 사유별 집계를 개수 내림차순 한 줄로 출력."""
    if not rejected:
        return
    summary = ", ".join(
        f"{k}: {v}"
        for k, v in sorted(rejected.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    logger.info(f"제외 사유 요약 - {summary}")


def parse_end_date(end_date_str: Optional[str]) -> Optional[datetime]:
    """Parse endDate string from Gamma API to datetime.

    Args:
        end_date_str: ISO format date string (e.g., "2025-12-31T12:00:00Z")

    Returns:
        datetime object or None if parsing fails
    """
    if not end_date_str:
        return None
    try:
        # Handle both formats: "2025-12-31T12:00:00Z" and "2025-12-31"
        if "T" in end_date_str:
            return datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        else:
            return datetime.fromisoformat(end_date_str + "T00:00:00+00:00")
    except (ValueError, TypeError):
        return None


def get_hours_until_resolution(end_date: Optional[datetime]) -> Optional[float]:
    """Calculate hours until market resolution.

    Args:
        end_date: Market end datetime

    Returns:
        Hours until resolution or None if end_date is None
    """
    if not end_date:
        return None
    now = datetime.now(timezone.utc)
    # DB에서 가져온 datetime이 timezone-naive일 수 있음 -> UTC로 처리
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    delta = end_date - now
    return delta.total_seconds() / 3600


class MarketScanner:
    """Scans markets for Fear Spike Fade buy candidates (항상 NO 토큰).

    Gamma 전체 sweep은 1회만 수행하고 Phase 0(스냅샷 저장)과
    Phase 2(스캔)가 결과를 공유한다 (banana의 2회 sweep 낭비 수정).
    """

    def __init__(
        self,
        gamma_client: GammaClient,
        config: TradingConfig,
        repo: Optional[TradeRepository] = None,
        history_client: Optional[HistoryClient] = None,
    ):
        """Initialize scanner.

        Args:
            gamma_client: Gamma API client
            config: Trading configuration
            repo: Trade repository (스냅샷 저장/조회에 필요)
            history_client: CLOB prices-history 백필 클라이언트 (optional)
        """
        self.gamma = gamma_client
        self.config = config
        self.repo = repo
        self.history = history_client

    def _signal_params(self) -> SignalParams:
        """config → 순수 함수용 SignalParams 변환."""
        strategy = self.config.strategy
        time_based = self.config.time_based
        return SignalParams(
            base_window_days=float(strategy.base_window_days),
            base_exclude_recent_hours=float(strategy.base_exclude_recent_hours),
            base_max=strategy.base_max,
            jump_min=strategy.jump_min,
            yes_max=strategy.yes_max,
            spike_wait_minutes=float(strategy.spike_wait_minutes),
            stall_window_minutes=float(strategy.stall_window_minutes),
            vol_mult_min=strategy.vol_mult_min,
            entry_hours_min=float(time_based.entry_hours_min),
            min_liquidity=self.config.min_liquidity,
            min_volume_24h=self.config.min_volume_24h,
        )

    def fetch_markets(self) -> List[Dict]:
        """Gamma 전체 sweep 1회 (Phase 0/2 공유)."""
        markets = self.gamma.get_all_tradable_markets(
            min_liquidity=self.config.min_liquidity
        )
        return markets

    def save_market_snapshots(self, markets: List[Dict]) -> int:
        """스캔 대상 시장의 스냅샷 저장 (Phase 0, YES 가격 기준).

        Args:
            markets: fetch_markets() 결과 (liquidity 필터 통과분)

        Returns:
            저장된 스냅샷 수
        """
        if not self.repo:
            logger.warning("Repository가 설정되지 않아 스냅샷 저장 불가")
            return 0

        saved = 0
        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            if is_sports_market(market, self.config.excluded_categories):
                continue

            side = get_no_side(market)
            if not side:
                continue

            # YES 가격 기준으로 저장 (시그널 판정 단위와 일치)
            self.repo.save_snapshot(
                condition_id=condition_id,
                probability=side["yes_price"],
                liquidity=float(market.get("liquidity") or 0),
                volume_24h=float(market.get("volume24hr") or 0),
            )
            saved += 1

        logger.info(f"스냅샷 {saved}개 저장 완료 (YES 가격 기준)")
        return saved

    def _collect_price_points(
        self,
        condition_id: str,
        yes_token_id: str,
        now: datetime,
    ) -> List:
        """DB 스냅샷 조회 + 윈도우 invalid 시 히스토리 백필 병합 (7일).

        백필이 실패해도(None) 조용히 DB 스냅샷만 반환한다.
        최종 유효성 판정은 evaluate_entry의 window_invalid가 담당.
        """
        lookback = float(self.config.strategy.base_window_days) * 24.0
        db_snapshots = self.repo.get_recent_snapshots(
            condition_id, hours_back=lookback, now=now
        ) if self.repo else []
        db_points = to_price_points(db_snapshots)

        window = get_window(db_points, lookback, now=now)
        if is_window_valid(window, lookback):
            return db_points

        if not self.config.history_backfill or not self.history:
            return db_points

        # cold start: CLOB /prices-history로 백필 시도 (naive UTC → to_unix_utc)
        backfill = self.history.get_price_history(
            token_id=yes_token_id,
            start=now - timedelta(hours=lookback),
            end=now,
        )
        if not backfill:
            return db_points

        merged = merge_price_points(db_points, backfill)
        logger.debug(
            f"백필 병합: DB {len(db_points)} + 히스토리 {len(backfill)} "
            f"-> {len(merged)}개 ({condition_id[:20]}...)"
        )
        return merged

    def _log_scan_summary(self, analysis: List[Dict]):
        """스캔 분석 요약 출력.

        Args:
            analysis: 분석 결과 리스트
        """
        if not analysis:
            logger.info("진입 대상 시장 없음")
            return

        logger.info("=" * 70)
        logger.info("Fear Spike Fade 스캔 요약 (공포 스파이크 페이드 = NO 매수)")
        logger.info("=" * 70)

        strategy = self.config.strategy
        time_based = self.config.time_based
        logger.info(
            f"설정: base <= {strategy.base_max:.0%} (7d 중앙값), "
            f"스파이크 >= +{strategy.jump_min:.2f} & YES <= {strategy.yes_max:.0%}, "
            f"대기 {strategy.spike_wait_minutes:.0f}분 + 스톨 {strategy.stall_window_minutes:.0f}분, "
            f"volume x{strategy.vol_mult_min:.1f}, "
            f"진입 해결시간 >= {time_based.entry_hours_min}h"
        )
        logger.info("-" * 70)

        entry_count = 0
        for item in analysis:
            status = "✓ 진입" if item["entry_signal"] else "✗ 제외"
            if item["entry_signal"]:
                entry_count += 1

            hours_str = f"{item['hours_left']:.1f}h" if item['hours_left'] is not None else "N/A"

            logger.info(
                f"{status} | YES {item['yes_price']:.1%} / NO {item['no_price']:.1%} | "
                f"해결까지: {hours_str} | 사유: {item['reason']}"
            )
            logger.info(f"       {item['question']}...")

        logger.info("-" * 70)
        logger.info(f"요약: 총 {len(analysis)}개 시장 중 {entry_count}개 진입 가능")
        logger.info("=" * 70)

    def scan_buy_candidates(self, markets: List[Dict]) -> List[Dict]:
        """Scan for markets meeting Fear Spike Fade buy criteria.

        Criteria (모두 충족):
        1. Not in excluded categories / liquidity >= min_liquidity
        2. hours_left >= 72 (마감 임박 스파이크 = 진짜 정보 가능성 배제)
        3. base = median(YES, [now-7d, now-6h]) <= 0.15
        4. 스파이크: yes_now - base >= 0.10 AND yes_now <= 0.30
        5. 스파이크 시작 90분 경과 + 최근 45분 신고가 없음
        6. volume24h >= 2 x 윈도우 평균
        7. 윈도우 유효성 통과 (7일 백필 포함) → NO 토큰 매수

        Args:
            markets: fetch_markets() 결과 (Phase 0과 공유)

        Returns:
            List of candidate dictionaries with market info
        """
        logger.info(f"시장 {len(markets)}개 스캔 시작")
        now = datetime.utcnow()
        params = self._signal_params()

        candidates = []
        scan_analysis = []  # 분석 결과 저장
        rejected = {}  # 사유 키 -> 개수 (요약 로그용)

        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            # Filter: Excluded categories
            if is_sports_market(market, self.config.excluded_categories):
                logger.debug(f"제외 카테고리 시장 skip: {condition_id}")
                rejected["excluded_category"] = rejected.get("excluded_category", 0) + 1
                continue

            # Filter: Liquidity (double check)
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                rejected["low_liquidity"] = rejected.get("low_liquidity", 0) + 1
                continue

            # NO 쪽 토큰 정보 (방향 내장 - 항상 NO)
            side = get_no_side(market)
            if not side or not side.get("token_id"):
                rejected["no_price_data"] = rejected.get("no_price_data", 0) + 1
                continue

            yes_price = side["yes_price"]
            no_price = side["no_price"]

            # 스파이크 자체가 불가능한 시장은 스냅샷/시그널 계산 없이 조용히 skip:
            # yes > yes_max면 러닝룸 없음, yes < jump_min이면 base>=0이어도 점프 미달
            # (로그 노이즈 + 불필요한 DB/백필 호출 방지)
            if yes_price > params.yes_max or yes_price < params.jump_min:
                rejected["spike_impossible"] = rejected.get("spike_impossible", 0) + 1
                continue

            end_date = parse_end_date(market.get("endDate"))
            hours_left = get_hours_until_resolution(end_date)

            # 스냅샷 + 백필 병합 (YES 가격 기준, 7일)
            snapshots = self._collect_price_points(
                condition_id, side["yes_token_id"], now
            )

            signal = evaluate_entry(
                yes_price=yes_price,
                liquidity=float(market.get("liquidity") or 0),
                volume_24h=float(market.get("volume24hr") or 0),
                hours_left=hours_left,
                snapshots=snapshots,
                params=params,
                now=now,
            )

            # 분석 결과 저장 (진입 여부와 관계없이)
            scan_analysis.append({
                "question": market.get("question", "")[:50],
                "yes_price": yes_price,
                "no_price": no_price,
                "hours_left": hours_left,
                "entry_signal": signal.entry,
                "reason": signal.reason,
            })

            if not signal.entry:
                key = _reason_key(signal.reason)
                rejected[key] = rejected.get(key, 0) + 1
                logger.debug(
                    f"진입 조건 미충족: {condition_id[:20]}... ({signal.reason})"
                )
                continue

            # Valid candidate (NO 토큰 매수)
            tags = market.get("tags") or []
            market_tags = ", ".join(
                t.get("label") or t.get("slug", "")
                for t in tags if isinstance(t, dict)
            )
            candidate = {
                "condition_id": condition_id,
                "market_slug": market.get("slug", ""),
                "question": market.get("question", ""),
                "outcome": side["outcome"],
                "probability": no_price,
                "yes_price": yes_price,
                "token_id": side["token_id"],
                "liquidity": float(market.get("liquidity") or 0),
                "volume_24h": float(market.get("volume24hr") or 0),
                "entry_reason": signal.reason,
                "end_date": end_date,
                "hours_until_resolution": hours_left,
                "market_tags": market_tags,
                # 시그널 수치 (DB *_at_buy 컬럼으로 기록 - 부록 §A)
                "base_price": signal.base_price,
                "spike_peak": signal.spike_peak,
                "spike_age_minutes": signal.spike_age_minutes,
                "vol_mult": signal.vol_mult,
            }
            candidates.append(candidate)
            logger.debug(
                f"매수 후보: {candidate['question'][:50]}... "
                f"(NO @ {no_price:.1%}, YES {yes_price:.1%}, base {signal.base_price:.1%}, "
                f"peak {signal.spike_peak:.1%}, 스파이크 {signal.spike_age_minutes:.0f}분 경과, "
                f"vol x{signal.vol_mult:.1f}, 사유: {signal.reason})"
            )

        # 스캔 분석 요약 출력
        self._log_scan_summary(scan_analysis)
        _log_reject_summary(rejected)

        logger.info(f"매수 후보 {len(candidates)}개 발견")
        return candidates
