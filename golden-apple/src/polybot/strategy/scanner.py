"""Market scanner for finding trading opportunities."""
import logging
from typing import List, Dict
from ..api.gamma_client import GammaClient
from ..config import TradingConfig
from .filters import (
    is_sports_market,
    passes_liquidity_filter,
    get_high_probability_outcome,
    is_valid_buy_candidate,
)

logger = logging.getLogger(__name__)


class MarketScanner:
    """Scans markets for buy candidates based on probability thresholds."""

    def __init__(self, gamma_client: GammaClient, config: TradingConfig):
        """Initialize scanner.

        Args:
            gamma_client: Gamma API client
            config: Trading configuration
        """
        self.gamma = gamma_client
        self.config = config

    def scan_buy_candidates(self) -> List[Dict]:
        """Scan for markets meeting buy criteria.

        Criteria:
        1. Not in excluded categories (sports)
        2. Liquidity >= min_liquidity
        3. Probability: buy_threshold <= prob < sell_threshold

        Returns:
            List of candidate dictionaries with market info
        """
        # Get all markets with minimum liquidity
        markets = self.gamma.get_all_tradable_markets(
            min_liquidity=self.config.min_liquidity
        )
        logger.info(f"Scanning {len(markets)} markets")

        candidates = []

        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            # Filter: Excluded categories (sports)
            if is_sports_market(market, self.config.excluded_categories):
                logger.debug(f"Skipping sports market: {condition_id}")
                continue

            # Filter: Liquidity (double check)
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                continue

            # Get high probability outcome
            outcome_info = get_high_probability_outcome(market)
            if not outcome_info or not outcome_info.get("token_id"):
                continue

            probability = outcome_info["probability"]

            # Filter: Probability in valid buy range
            if not is_valid_buy_candidate(
                probability,
                self.config.buy_threshold,
                self.config.sell_threshold,
            ):
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
            }
            candidates.append(candidate)
            logger.debug(
                f"Buy candidate: {candidate['question'][:50]}... "
                f"({candidate['outcome']} @ {probability:.1%})"
            )

        logger.info(f"Found {len(candidates)} buy candidates")
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
            logger.error(f"Failed to get price for {token_id}: {e}")
            return 0.0
