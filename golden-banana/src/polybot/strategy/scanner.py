"""Market scanner for finding trading opportunities."""
import logging
from typing import List, Dict, Optional
from ..api.gamma_client import GammaClient
from ..config import TradingConfig
from ..db.repository import TradeRepository
from .filters import (
    is_sports_market,
    passes_liquidity_filter,
    get_high_probability_outcome,
    is_valid_buy_candidate,
)
from .momentum import MomentumCalculator

logger = logging.getLogger(__name__)


class MarketScanner:
    """Scans markets for buy candidates based on probability thresholds and momentum."""

    def __init__(
        self,
        gamma_client: GammaClient,
        config: TradingConfig,
        repo: Optional[TradeRepository] = None
    ):
        """Initialize scanner.

        Args:
            gamma_client: Gamma API client
            config: Trading configuration
            repo: Trade repository for snapshot access (optional, required for momentum)
        """
        self.gamma = gamma_client
        self.config = config
        self.repo = repo

        # Initialize momentum calculator if enabled
        self.momentum_calc = None
        if config.momentum.enabled:
            self.momentum_calc = MomentumCalculator(config.momentum)

    def _log_momentum_summary(self, analysis: List[Dict]):
        """모멘텀 분석 요약 출력.

        Args:
            analysis: 모멘텀 분석 결과 리스트
        """
        if not analysis:
            logger.info("모멘텀 분석 대상 시장 없음 (확률 조건 충족 시장 없음)")
            return

        logger.info("=" * 70)
        logger.info("모멘텀 분석 요약")
        logger.info("=" * 70)

        if self.momentum_calc:
            logger.info(
                f"설정: 골든크로스 >= {self.config.momentum.golden_cross_threshold}, "
                f"단기={self.config.momentum.short_window}, 장기={self.config.momentum.long_window}"
            )
        else:
            logger.info("설정: 모멘텀 비활성화 (확률 조건만 사용)")

        logger.info("-" * 70)

        entry_count = 0
        for item in analysis:
            status = "✓ 진입" if item["entry_signal"] else "✗ 제외"
            if item["entry_signal"]:
                entry_count += 1

            short = f"{item['short_momentum']:.6f}" if item['short_momentum'] is not None else "N/A"
            long_m = f"{item['long_momentum']:.6f}" if item['long_momentum'] is not None else "N/A"
            diff = f"{item['diff']:+.6f}" if item['diff'] is not None else "N/A"

            snapshot_cnt = item.get('snapshot_count', 0)
            logger.info(
                f"{status} | {item['outcome']} @ {item['probability']:.1%} | "
                f"스냅샷: {snapshot_cnt}개 | 단기: {short} | 장기: {long_m} | 차이: {diff} | "
                f"사유: {item['reason']}"
            )
            logger.info(f"       {item['question']}...")

        logger.info("-" * 70)
        logger.info(f"요약: 총 {len(analysis)}개 시장 중 {entry_count}개 진입 가능")
        logger.info("=" * 70)

    def scan_buy_candidates(self) -> List[Dict]:
        """Scan for markets meeting buy criteria.

        Criteria:
        1. Not in excluded categories (sports)
        2. Liquidity >= min_liquidity
        3. Probability: buy_threshold <= prob <= sell_threshold
        4. Momentum: Golden cross (if enabled)

        Returns:
            List of candidate dictionaries with market info
        """
        # Get all markets with minimum liquidity
        markets = self.gamma.get_all_tradable_markets(
            min_liquidity=self.config.min_liquidity
        )
        logger.info(f"시장 {len(markets)}개 스캔 시작")

        candidates = []
        momentum_analysis = []  # 모멘텀 분석 결과 저장

        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            # Filter: Excluded categories (sports)
            if is_sports_market(market, self.config.excluded_categories):
                logger.debug(f"스포츠 시장 제외: {condition_id}")
                continue

            # Filter: Liquidity (double check)
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                continue

            # Get high probability outcome
            outcome_info = get_high_probability_outcome(market)
            if not outcome_info or not outcome_info.get("token_id"):
                continue

            probability = outcome_info["probability"]

            # Filter: Probability in valid buy range (85% <= prob <= 97%)
            if not is_valid_buy_candidate(
                probability,
                self.config.buy_threshold,
                self.config.sell_threshold,
            ):
                continue

            # Filter: Momentum signal (if enabled)
            entry_signal = True
            entry_reason = "momentum_disabled"
            short_momentum = None
            long_momentum = None

            snapshot_count = 0
            if self.momentum_calc and self.repo:
                # Get snapshots for momentum calculation
                snapshots = self.repo.get_snapshots_for_condition(
                    condition_id,
                    limit=self.config.momentum.long_window + 10
                )
                snapshot_count = len(snapshots)
                entry_signal, entry_reason = self.momentum_calc.get_entry_signal(
                    snapshots, probability
                )
                # 모멘텀 정보 수집
                short_momentum, long_momentum = self.momentum_calc.get_momentum_info(snapshots)

            # 모멘텀 분석 결과 저장 (진입 여부와 관계없이)
            diff = None
            if short_momentum is not None and long_momentum is not None:
                diff = short_momentum - long_momentum

            momentum_analysis.append({
                "question": market.get("question", "")[:50],
                "outcome": outcome_info["outcome"],
                "probability": probability,
                "short_momentum": short_momentum,
                "long_momentum": long_momentum,
                "diff": diff,
                "entry_signal": entry_signal,
                "reason": entry_reason,
                "snapshot_count": snapshot_count,
            })

            if not entry_signal:
                logger.debug(
                    f"모멘텀 조건 미충족: {condition_id[:20]}... ({entry_reason})"
                )
                continue

            # Valid candidate
            candidate = {
                "condition_id": condition_id,
                "market_slug": market.get("slug", ""),
                "question": market.get("question", ""),
                "outcome": outcome_info["outcome"],
                "probability": probability,
                "token_id": outcome_info["token_id"],
                "liquidity": float(market.get("liquidity") or 0),
                "entry_reason": entry_reason,  # 진입 사유 추가
            }
            candidates.append(candidate)
            logger.debug(
                f"매수 후보: {candidate['question'][:50]}... "
                f"({candidate['outcome']} @ {probability:.1%}, 사유: {entry_reason})"
            )

        # 모멘텀 분석 요약 출력
        self._log_momentum_summary(momentum_analysis)

        logger.info(f"매수 후보 {len(candidates)}개 발견")
        return candidates

    def save_market_snapshots(self) -> int:
        """모든 추적 대상 마켓의 스냅샷 저장.

        5분마다 호출되어 모멘텀 계산에 필요한 데이터 축적.

        Returns:
            저장된 스냅샷 수
        """
        if not self.repo:
            logger.warning("Repository가 설정되지 않아 스냅샷 저장 불가")
            return 0

        markets = self.gamma.get_all_tradable_markets(
            min_liquidity=self.config.min_liquidity
        )

        saved = 0
        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            # Skip sports markets
            if is_sports_market(market, self.config.excluded_categories):
                continue

            # Get probability
            outcome_info = get_high_probability_outcome(market)
            if not outcome_info:
                continue

            # Save snapshot
            self.repo.save_snapshot(
                condition_id=condition_id,
                probability=outcome_info["probability"],
                liquidity=float(market.get("liquidity") or 0),
                volume_24h=float(market.get("volume24hr") or 0),
            )
            saved += 1

        logger.info(f"스냅샷 {saved}개 저장 완료")
        return saved

    def check_current_price(self, token_id: str, clob_client) -> float:
        """Get current price for a token.

        Args:
            token_id: Token ID
            clob_client: CLOB client for price queries

        Returns:
            Current midpoint price or 0.0 on error
        """
        try:
            return clob_client.get_midpoint(token_id)
        except Exception as e:
            logger.error(f"가격 조회 실패 - token: {token_id}: {e}")
            return 0.0
