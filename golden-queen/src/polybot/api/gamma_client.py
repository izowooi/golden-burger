"""Gamma API client for market data retrieval."""
import hashlib
import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional
from uuid import uuid4

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
    CONNECT_TIMEOUT_SECONDS = 3.05
    READ_TIMEOUT_SECONDS = 20.0
    MAX_SWEEP_PAGES = 10_000
    KEYSET_PAGE_INTERVAL_SECONDS = 0.25
    SWEEP_SCHEMA_VERSION = 1

    def __init__(self):
        self.session = requests.Session()
        self.sweep_attestations: List[Dict] = []
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenQueen-PolyBot/1.0"
        })

    def _get(self, path: str, *, params: Optional[Dict] = None):
        """Issue a bounded Gamma request with separate connect/read limits."""
        return self.session.get(
            f"{self.BASE_URL}{path}",
            params=params,
            timeout=(self.CONNECT_TIMEOUT_SECONDS, self.READ_TIMEOUT_SECONDS),
        )

    @rate_limit_handler(
        max_retries=6,
        base_delay=2.0,
        retry_forbidden=True,
    )
    def _get_keyset_page(self, params: Dict):
        """Fetch one keyset page so transient 403/429 retries keep the cursor."""
        response = self._get("/markets/keyset", params=params)
        response.raise_for_status()
        return response

    @property
    def last_sweep_attestation(self) -> Optional[Dict]:
        """Return the last fully completed keyset sweep, never a partial one."""
        return self.sweep_attestations[-1] if self.sweep_attestations else None

    def get_sweep_summaries(self) -> List[Dict]:
        """Return RunAudit-safe summaries without the potentially large membership list."""
        return [
            {key: value for key, value in attestation.items() if key != "memberships"}
            for attestation in self.sweep_attestations
        ]

    @staticmethod
    def _qualification_reason(
        market: Dict,
        min_liquidity: float,
        min_volume: float,
    ) -> str:
        """Return the first fail-closed Gamma universe exclusion reason."""
        if market.get("active") is not True:
            return "inactive_or_missing"
        if market.get("closed") is not False:
            return "closed_or_missing"
        if market.get("enableOrderBook") is not True:
            return "order_book_disabled_or_missing"
        if market.get("acceptingOrders") is not True:
            return "orders_not_accepted_or_missing"
        raw_liquidity = market.get("liquidity")
        raw_volume = market.get("volume")
        if any(
            raw is None
            or isinstance(raw, bool)
            or (isinstance(raw, str) and not raw.strip())
            for raw in (raw_liquidity, raw_volume)
        ):
            return "invalid_numeric_filter_field"
        try:
            liquidity = float(raw_liquidity)
            volume = float(raw_volume)
        except (TypeError, ValueError):
            return "invalid_numeric_filter_field"
        if (
            not math.isfinite(liquidity)
            or not math.isfinite(volume)
            or liquidity < 0
            or volume < 0
        ):
            return "invalid_numeric_filter_field"
        if liquidity < min_liquidity:
            return "below_min_liquidity"
        if volume < min_volume:
            return "below_min_volume"
        return "qualified"

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

        response = self._get("/markets", params=params)
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

    def get_all_tradable_markets(
        self,
        min_liquidity: float = 0,
        min_volume: float = 0,
    ) -> List[Dict]:
        """Get the complete tradeable universe with cursor pagination.

        Gamma's keyset endpoint avoids the offset ceiling and returns event/tag
        metadata needed to reproduce the scanned universe in retrospectives.
        """
        min_liquidity = float(min_liquidity)
        min_volume = float(min_volume)
        if (
            not math.isfinite(min_liquidity)
            or not math.isfinite(min_volume)
            or min_liquidity < 0
            or min_volume < 0
        ):
            raise ValueError("Gamma sweep filters must be finite and non-negative")
        started_at = datetime.now(timezone.utc)
        sweep_id = str(uuid4())
        by_condition: Dict[str, Dict] = {}
        memberships: Dict[str, Dict] = {}
        cursor: Optional[str] = None
        seen_cursors = set()
        pages = 0
        raw_market_count = 0
        missing_condition_id_count = 0

        while True:
            params = {
                "closed": "false",
                "include_tag": "true",
                "limit": 100,
            }
            if min_liquidity > 0:
                params["liquidity_num_min"] = min_liquidity
            if min_volume > 0:
                params["volume_num_min"] = min_volume
            if cursor:
                params["after_cursor"] = cursor

            response = self._get_keyset_page(params)
            payload = response.json()
            raw_markets = payload.get("markets", [])
            if not isinstance(raw_markets, list):
                raise ValueError("Gamma keyset 응답의 markets가 list가 아닙니다")

            for raw_market in raw_markets:
                raw_market_count += 1
                market = self._parse_market(raw_market)
                condition_id = market.get("conditionId")
                if not condition_id:
                    missing_condition_id_count += 1
                    continue
                condition_id = str(condition_id)
                membership = memberships.setdefault(
                    condition_id,
                    {
                        "condition_id": condition_id,
                        "raw_seen_count": 0,
                        "qualified": False,
                        "qualification_reason": None,
                    },
                )
                membership["raw_seen_count"] += 1
                reason = self._qualification_reason(
                    market, min_liquidity=min_liquidity, min_volume=min_volume
                )
                if reason == "qualified":
                    membership["qualified"] = True
                    membership["qualification_reason"] = "qualified"
                    by_condition[condition_id] = market
                elif not membership["qualified"]:
                    membership["qualification_reason"] = reason

            pages += 1
            next_cursor = payload.get("next_cursor")
            if not next_cursor:
                break
            if pages >= self.MAX_SWEEP_PAGES:
                raise RuntimeError(
                    f"Gamma keyset 순회가 {self.MAX_SWEEP_PAGES}페이지 한도를 초과했습니다"
                )
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise RuntimeError("Gamma keyset cursor가 반복되어 순회를 중단합니다")
            seen_cursors.add(str(next_cursor))
            cursor = str(next_cursor)
            time.sleep(self.KEYSET_PAGE_INTERVAL_SECONDS)

        markets = list(by_condition.values())
        sorted_memberships = sorted(
            memberships.values(), key=lambda item: item["condition_id"]
        )
        qualified_memberships = [
            item for item in sorted_memberships if item["qualified"]
        ]
        exclusion_counts: Dict[str, int] = {}
        for item in sorted_memberships:
            if item["qualified"]:
                continue
            reason = item["qualification_reason"]
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
        membership_bytes = json.dumps(
            qualified_memberships,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        attestation = {
            "schema_version": self.SWEEP_SCHEMA_VERSION,
            "sweep_id": sweep_id,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "cursor_complete": True,
            "pages": pages,
            "raw_market_count": raw_market_count,
            "unique_condition_count": len(memberships),
            "qualified_market_count": len(markets),
            "excluded_condition_count": len(memberships) - len(markets),
            "exclusion_counts": dict(sorted(exclusion_counts.items())),
            "missing_condition_id_count": missing_condition_id_count,
            "duplicate_raw_count": (
                raw_market_count - missing_condition_id_count - len(memberships)
            ),
            "min_liquidity": float(min_liquidity),
            "min_volume": float(min_volume),
            "membership_digest_sha256": hashlib.sha256(membership_bytes).hexdigest(),
            "membership_digest_scope": "qualified_only",
            "memberships": sorted_memberships,
        }
        self.sweep_attestations.append(attestation)
        logger.info(
            f"시장 {len(markets)}개 조회 완료 "
            f"(keyset {pages}페이지, 유동성 >= ${min_liquidity:,.0f})"
        )
        return markets

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
            response = self._get("/markets", params=params)
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
            response = self._get(f"/events/{event_id}")
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
