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
                    logger.warning(f"{field} 파싱 실패 - market: {market.get('conditionId')}")
        return market

    @rate_limit_handler(max_retries=3)
    def _fetch_markets_page(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        """Fetch a single page of active markets from the Gamma API.

        Args:
            limit: Maximum number of markets to return (max 100)
            offset: Pagination offset

        Returns:
            List of raw market dictionaries from API
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
        return [self._parse_market(m) for m in markets]

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
            List of market dictionaries passing the liquidity filter
        """
        parsed = self._fetch_markets_page(limit=limit, offset=offset)

        # Filter by liquidity
        if min_liquidity > 0:
            parsed = [
                m for m in parsed
                if float(m.get("liquidity") or 0) >= min_liquidity
            ]

        return parsed

    def get_all_tradable_markets(
        self,
        min_liquidity: float = 0,
        min_volume: float = 0,
    ) -> List[Dict]:
        """Get all tradeable markets with pagination.

        Args:
            min_liquidity: Minimum liquidity filter
            min_volume: Minimum cumulative volume filter (0 = disabled)

        Returns:
            List of all active markets meeting criteria
        """
        all_markets = []
        offset = 0
        limit = 100

        while True:
            # Fetch raw page for accurate pagination decision
            raw_markets = self._fetch_markets_page(limit=limit, offset=offset)

            if not raw_markets:
                break

            # Apply client-side filters
            filtered = raw_markets
            if min_liquidity > 0:
                filtered = [
                    m for m in filtered
                    if float(m.get("liquidity") or 0) >= min_liquidity
                ]
            if min_volume > 0:
                filtered = [
                    m for m in filtered
                    if float(m.get("volume") or 0) >= min_volume
                ]

            logger.debug(
                f"페이지 offset={offset}: API {len(raw_markets)}개 -> 필터 통과 {len(filtered)}개"
            )

            all_markets.extend(filtered)
            offset += limit

            # If API returned fewer than requested, this is the last page
            if len(raw_markets) < limit:
                break

            # Safety limit to prevent infinite loops
            if offset >= 5000:
                logger.warning("최대 페이지네이션 한도 도달")
                break

        logger.info(
            f"시장 {len(all_markets)}개 조회 완료 "
            f"(유동성 >= ${min_liquidity:,.0f}, 거래량 >= ${min_volume:,.0f})"
        )
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
            logger.error(f"시장 조회 실패 - condition: {condition_id}: {e}")
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
            logger.error(f"이벤트 조회 실패 - event: {event_id}: {e}")
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
