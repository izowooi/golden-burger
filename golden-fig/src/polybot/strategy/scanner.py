"""Market scanner for finding trading opportunities (Hope Crusher strategy)."""
import logging
from datetime import datetime, timezone
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
)

logger = logging.getLogger(__name__)


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
    """Scans markets for Hope Crusher buy candidates (항상 NO 토큰).

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
            yes_min=strategy.yes_min,
            yes_max=strategy.yes_max,
            yes_rise_block_24h=strategy.yes_rise_block_24h,
            yes_spike_block_6h=strategy.yes_spike_block_6h,
            entry_hours_min=float(time_based.entry_hours_min),
            entry_hours_max=float(time_based.entry_hours_max),
            min_liquidity=self.config.min_liquidity,
            min_volume_24h=self.config.min_volume_24h,
            rise_lookback_hours=float(strategy.rise_lookback_hours),
            spike_lookback_hours=float(strategy.spike_lookback_hours),
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
        """DB 스냅샷 조회 + 윈도우 invalid 시 히스토리 백필 병합.

        백필이 실패해도(None) 조용히 DB 스냅샷만 반환한다.
        최종 유효성 판정은 evaluate_entry의 window_invalid가 담당.
        """
        lookback = float(self.config.strategy.rise_lookback_hours)
        db_points = self.repo.get_recent_snapshots(
            condition_id, hours_back=lookback, now=now
        ) if self.repo else []

        window = get_window(db_points, lookback, now=now)
        if is_window_valid(window, lookback):
            return db_points

        if not self.config.history_backfill or not self.history:
            return db_points

        # cold start: CLOB /prices-history로 백필 시도
        end_ts = int(now.replace(tzinfo=timezone.utc).timestamp())
        start_ts = end_ts - int(lookback * 3600)
        backfill = self.history.get_price_history(
            token_id=yes_token_id,
            start_ts=start_ts,
            end_ts=end_ts,
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
        logger.info("Hope Crusher 스캔 요약 (롱샷 희망 페이드 = NO 매수)")
        logger.info("=" * 70)

        strategy = self.config.strategy
        time_based = self.config.time_based
        logger.info(
            f"설정: YES {strategy.yes_min:.0%}~{strategy.yes_max:.0%} → NO 매수, "
            f"진입 {time_based.entry_hours_min}h <= 해결시간 <= {time_based.entry_hours_max}h, "
            f"24h 상승 차단 > {strategy.yes_rise_block_24h:+.2f}, "
            f"6h 급등 차단 >= {strategy.yes_spike_block_6h:.2f}"
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
        """Scan for markets meeting Hope Crusher buy criteria.

        Criteria (모두 충족):
        1. Not in excluded categories / liquidity >= min_liquidity
        2. 24 <= hours_left <= 240
        3. YES 가격 ∈ [0.05, 0.25] → NO 토큰 매수 (NO 가격 ∈ [0.75, 0.95])
        4. 사건 진행 배제: YES 24h 변화 <= +0.02 AND 최근 6h YES 급등 < 0.05
        5. 윈도우 유효성 통과 (백필 포함)

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

        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            # Filter: Excluded categories
            if is_sports_market(market, self.config.excluded_categories):
                logger.debug(f"제외 카테고리 시장 skip: {condition_id}")
                continue

            # Filter: Liquidity (double check)
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                continue

            # NO 쪽 토큰 정보 (방향 내장 - 항상 NO)
            side = get_no_side(market)
            if not side or not side.get("token_id"):
                continue

            yes_price = side["yes_price"]
            no_price = side["no_price"]

            # 밴드 밖 시장은 스냅샷/시그널 계산 없이 조용히 skip (로그 노이즈 방지)
            if not (params.yes_min <= yes_price <= params.yes_max):
                continue

            end_date = parse_end_date(market.get("endDate"))
            hours_left = get_hours_until_resolution(end_date)

            # 스냅샷 + 백필 병합 (YES 가격 기준)
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
            }
            candidates.append(candidate)
            logger.debug(
                f"매수 후보: {candidate['question'][:50]}... "
                f"(NO @ {no_price:.1%}, YES {yes_price:.1%}, "
                f"해결까지 {hours_left:.1f}h, 사유: {signal.reason})"
            )

        # 스캔 분석 요약 출력
        self._log_scan_summary(scan_analysis)

        logger.info(f"매수 후보 {len(candidates)}개 발견")
        return candidates
