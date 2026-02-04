"""Market scanner for finding trading opportunities."""
import logging
from datetime import datetime, timezone
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
    delta = end_date - now
    return delta.total_seconds() / 3600


def is_valid_time_entry(
    end_date: Optional[datetime],
    entry_hours_max: int,
    entry_hours_min: int
) -> tuple[bool, str, Optional[float]]:
    """Check if market is within valid time window for entry.

    Args:
        end_date: Market resolution datetime
        entry_hours_max: Maximum hours until resolution (24)
        entry_hours_min: Minimum hours until resolution (4)

    Returns:
        Tuple of (is_valid, reason, hours_left)
    """
    hours_left = get_hours_until_resolution(end_date)

    if hours_left is None:
        return False, "no_end_date", None

    if hours_left <= 0:
        return False, "already_resolved", hours_left

    if hours_left > entry_hours_max:
        return False, f"too_early_{hours_left:.1f}h", hours_left

    if hours_left < entry_hours_min:
        return False, f"too_late_{hours_left:.1f}h", hours_left

    return True, f"time_based_{hours_left:.1f}h", hours_left


class MarketScanner:
    """Scans markets for buy candidates based on time-to-resolution strategy."""

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
            repo: Trade repository (optional)
        """
        self.gamma = gamma_client
        self.config = config
        self.repo = repo

    def _log_scan_summary(self, analysis: List[Dict]):
        """스캔 분석 요약 출력.

        Args:
            analysis: 분석 결과 리스트
        """
        if not analysis:
            logger.info("진입 대상 시장 없음")
            return

        logger.info("=" * 70)
        logger.info("시간 기반 스캔 요약 (Resolution Momentum Strategy)")
        logger.info("=" * 70)

        time_cfg = self.config.time_based
        logger.info(
            f"설정: 진입 조건 {time_cfg.entry_hours_min}h < 해결시간 <= {time_cfg.entry_hours_max}h, "
            f"확률 {self.config.buy_threshold:.0%} ~ {self.config.sell_threshold:.0%}"
        )
        logger.info("-" * 70)

        entry_count = 0
        for item in analysis:
            status = "✓ 진입" if item["entry_signal"] else "✗ 제외"
            if item["entry_signal"]:
                entry_count += 1

            hours_str = f"{item['hours_left']:.1f}h" if item['hours_left'] is not None else "N/A"

            logger.info(
                f"{status} | {item['outcome']} @ {item['probability']:.1%} | "
                f"해결까지: {hours_str} | 사유: {item['reason']}"
            )
            logger.info(f"       {item['question']}...")

        logger.info("-" * 70)
        logger.info(f"요약: 총 {len(analysis)}개 시장 중 {entry_count}개 진입 가능")
        logger.info("=" * 70)

    def scan_buy_candidates(self) -> List[Dict]:
        """Scan for markets meeting buy criteria.

        Criteria (Resolution Momentum Strategy):
        1. Not in excluded categories (sports)
        2. Liquidity >= min_liquidity
        3. Probability: buy_threshold <= prob <= sell_threshold (75-92%)
        4. Time: entry_hours_min < hours_until_resolution <= entry_hours_max (4-24h)

        Returns:
            List of candidate dictionaries with market info
        """
        # Get all markets with minimum liquidity
        markets = self.gamma.get_all_tradable_markets(
            min_liquidity=self.config.min_liquidity
        )
        logger.info(f"시장 {len(markets)}개 스캔 시작")

        candidates = []
        scan_analysis = []  # 분석 결과 저장

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

            # Filter: Probability in valid buy range (75% <= prob <= 92%)
            if not is_valid_buy_candidate(
                probability,
                self.config.buy_threshold,
                self.config.sell_threshold,
            ):
                continue

            # Filter: Time-based entry (if enabled)
            entry_signal = True
            entry_reason = "probability_only"
            hours_left = None
            end_date = None

            if self.config.time_based.enabled:
                end_date = parse_end_date(market.get("endDate"))
                entry_signal, entry_reason, hours_left = is_valid_time_entry(
                    end_date,
                    self.config.time_based.entry_hours_max,
                    self.config.time_based.entry_hours_min
                )

            # 분석 결과 저장 (진입 여부와 관계없이)
            scan_analysis.append({
                "question": market.get("question", "")[:50],
                "outcome": outcome_info["outcome"],
                "probability": probability,
                "hours_left": hours_left,
                "entry_signal": entry_signal,
                "reason": entry_reason,
            })

            if not entry_signal:
                logger.debug(
                    f"시간 조건 미충족: {condition_id[:20]}... ({entry_reason})"
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
                "entry_reason": entry_reason,
                "end_date": end_date,
                "hours_until_resolution": hours_left,
            }
            candidates.append(candidate)
            logger.debug(
                f"매수 후보: {candidate['question'][:50]}... "
                f"({candidate['outcome']} @ {probability:.1%}, "
                f"해결까지 {hours_left:.1f}h, 사유: {entry_reason})"
            )

        # 스캔 분석 요약 출력
        self._log_scan_summary(scan_analysis)

        logger.info(f"매수 후보 {len(candidates)}개 발견")
        return candidates

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
