"""Gamma API client for market data retrieval."""
import json
import logging
from typing import List, Dict, Optional
import requests
from ..utils.retry import rate_limit_handler

logger = logging.getLogger(__name__)


class GammaClient:
    """Client for Polymarket Gamma API (market metadata).

    Gamma API provides:
    - Market listings and metadata
    - Category/tag information
    - Outcome prices and liquidity
    - No authentication required for read operations
    """

    BASE_URL = "https://gamma-api.polymarket.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenApple-PolyBot/1.0"
        })

    def _parse_market(self, market: Dict) -> Dict:
        """Parse JSON string fields in market data."""
        json_fields = ["outcomePrices", "clobTokenIds", "outcomes"]
        for field in json_fields:
            if field in market and isinstance(market[field], str):
                try:
                    market[field] = json.loads(market[field])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse {field} for market {market.get('conditionId')}")
        return market

    @rate_limit_handler(max_retries=3)
    def get_active_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        min_liquidity: float = 0,
    ) -> List[Dict]:
        """Get list of active, tradeable markets.

        Args:
            limit: Maximum number of markets to return (max 100)
            offset: Pagination offset
            min_liquidity: Minimum liquidity filter

        Returns:
            List of market dictionaries
        """
        params = {
            "active": "true",
            "closed": "false",
            "limit": min(limit, 100),
            "offset": offset,
        }

        response = self.session.get(f"{self.BASE_URL}/markets", params=params)
        response.raise_for_status()

        markets = response.json()
        parsed = [self._parse_market(m) for m in markets]

        # Filter by liquidity
        if min_liquidity > 0:
            parsed = [
                m for m in parsed
                if float(m.get("liquidity") or 0) >= min_liquidity
            ]

        return parsed

    def get_all_tradable_markets(self, min_liquidity: float = 0) -> List[Dict]:
        """Get all tradeable markets with pagination.

        Args:
            min_liquidity: Minimum liquidity filter

        Returns:
            List of all active markets meeting criteria
        """
        all_markets = []
        offset = 0
        limit = 100

        while True:
            markets = self.get_active_markets(
                limit=limit,
                offset=offset,
                min_liquidity=min_liquidity,
            )

            if not markets:
                break

            all_markets.extend(markets)
            offset += limit

            # Safety limit to prevent infinite loops
            if offset >= 5000:
                logger.warning("Reached maximum pagination limit")
                break

        logger.info(f"Retrieved {len(all_markets)} markets with liquidity >= ${min_liquidity:,.0f}")
        return all_markets

    @rate_limit_handler(max_retries=3)
    def get_market_by_condition_id(self, condition_id: str) -> Optional[Dict]:
        """Get market details by condition ID.

        Args:
            condition_id: Market condition ID

        Returns:
            Market dictionary or None if not found
        """
        params = {"condition_ids": condition_id, "limit": 1}

        try:
            response = self.session.get(f"{self.BASE_URL}/markets", params=params)
            response.raise_for_status()

            markets = response.json()
            if markets:
                return self._parse_market(markets[0])
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get market {condition_id}: {e}")
            return None

    @rate_limit_handler(max_retries=3)
    def get_event_by_id(self, event_id: str) -> Optional[Dict]:
        """Get event details including tags/categories.

        Args:
            event_id: Event ID

        Returns:
            Event dictionary or None
        """
        try:
            response = self.session.get(f"{self.BASE_URL}/events/{event_id}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get event {event_id}: {e}")
            return None

    def get_market_tags(self, market: Dict) -> List[str]:
        """Extract category tags from market data.

        Args:
            market: Market dictionary

        Returns:
            List of tag slugs
        """
        tags = market.get("tags", [])
        if isinstance(tags, list):
            return [
                tag.get("slug", "") if isinstance(tag, dict) else str(tag)
                for tag in tags
            ]
        return []
