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


class GammaConditionMismatchError(RuntimeError):
    """Gamma returned a non-empty market that does not match the requested ID."""


class GammaSweepBudgetExceeded(RuntimeError):
    """A complete keyset sweep cannot fit inside its frozen cadence budget."""


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
    SWEEP_SCHEMA_VERSION = 2

    def __init__(self):
        self.session = requests.Session()
        self.sweep_attestations: List[Dict] = []
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenKiwi-PolyBot/1.0"
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
        *,
        max_pages: Optional[int] = None,
        max_markets: Optional[int] = None,
        max_elapsed_seconds: Optional[float] = None,
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
        effective_max_pages = self.MAX_SWEEP_PAGES if max_pages is None else max_pages
        if (
            isinstance(effective_max_pages, bool)
            or not isinstance(effective_max_pages, int)
            or not 0 < effective_max_pages <= self.MAX_SWEEP_PAGES
        ):
            raise ValueError(
                "Gamma max_pages must be a positive integer within the hard limit"
            )
        if (
            max_markets is not None
            and (
                isinstance(max_markets, bool)
                or not isinstance(max_markets, int)
                or max_markets <= 0
            )
        ):
            raise ValueError("Gamma max_markets must be a positive integer")
        if max_elapsed_seconds is not None:
            max_elapsed_seconds = float(max_elapsed_seconds)
            if (
                not math.isfinite(max_elapsed_seconds)
                or max_elapsed_seconds <= 0
            ):
                raise ValueError(
                    "Gamma max_elapsed_seconds must be finite and positive"
                )
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        sweep_id = str(uuid4())
        by_condition: Dict[str, Dict] = {}
        memberships: Dict[str, Dict] = {}
        cursor: Optional[str] = None
        seen_cursors = set()
        pages = 0
        raw_market_count = 0
        missing_condition_id_count = 0

        while True:
            elapsed_seconds = time.monotonic() - started_monotonic
            if (
                max_elapsed_seconds is not None
                and elapsed_seconds > max_elapsed_seconds
            ):
                raise GammaSweepBudgetExceeded(
                    "Gamma keyset sweep exceeded the elapsed-time budget before "
                    f"page {pages + 1}: {elapsed_seconds:.3f}s > "
                    f"{max_elapsed_seconds:.3f}s"
                )
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
            # Keep the local receipt clock for each page. A full keyset sweep
            # can take minutes, so stamping every market at sweep completion
            # would fabricate a tighter cadence than was observed.
            page_observed_at = datetime.now(timezone.utc).isoformat()

            for raw_market in raw_markets:
                if not isinstance(raw_market, dict):
                    raise ValueError("Gamma keyset market must be an object")
                raw_market["_gammaObservedAt"] = page_observed_at
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
            elapsed_seconds = time.monotonic() - started_monotonic
            if max_markets is not None and raw_market_count > max_markets:
                raise GammaSweepBudgetExceeded(
                    "Gamma keyset sweep exceeded the raw-market budget: "
                    f"{raw_market_count} > {max_markets}"
                )
            if (
                max_elapsed_seconds is not None
                and elapsed_seconds > max_elapsed_seconds
            ):
                raise GammaSweepBudgetExceeded(
                    "Gamma keyset sweep exceeded the elapsed-time budget: "
                    f"{elapsed_seconds:.3f}s > {max_elapsed_seconds:.3f}s"
                )
            next_cursor = payload.get("next_cursor")
            if not next_cursor:
                break
            if pages >= effective_max_pages:
                raise GammaSweepBudgetExceeded(
                    "Gamma keyset sweep requires more than the page budget: "
                    f"> {effective_max_pages} pages"
                )
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise RuntimeError("Gamma keyset cursor가 반복되어 순회를 중단합니다")
            seen_cursors.add(str(next_cursor))
            cursor = str(next_cursor)
            time.sleep(self.KEYSET_PAGE_INTERVAL_SECONDS)

        elapsed_seconds = time.monotonic() - started_monotonic
        if (
            max_elapsed_seconds is not None
            and elapsed_seconds > max_elapsed_seconds
        ):
            raise GammaSweepBudgetExceeded(
                "Gamma keyset sweep exceeded the elapsed-time budget before "
                f"attestation: {elapsed_seconds:.3f}s > "
                f"{max_elapsed_seconds:.3f}s"
            )
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
            "max_pages": int(effective_max_pages),
            "max_markets": max_markets,
            "max_elapsed_seconds": max_elapsed_seconds,
            "elapsed_seconds": elapsed_seconds,
            "membership_digest_sha256": hashlib.sha256(membership_bytes).hexdigest(),
            "membership_digest_scope": "qualified_only",
            "memberships": sorted_memberships,
        }
        self.sweep_attestations.append(attestation)
        logger.info(
            f"시장 {len(markets)}개 조회 완료 "
            f"(keyset {pages}페이지, 유동성 >= ${min_liquidity:,.0f}, "
            f"누적 거래량 >= ${min_volume:,.0f}, {elapsed_seconds:.1f}초)"
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
        except requests.exceptions.RequestException as error:
            logger.error(
                "시장 조회 실패 - condition: %s: %s",
                condition_id,
                error,
            )
            # The retry decorator handles bounded transient retries.  After
            # exhaustion, the repository records this as a lookup error rather
            # than conflating transport failure with a genuine empty result.
            raise

        markets = response.json()
        if not isinstance(markets, list):
            raise GammaConditionMismatchError(
                "Gamma condition lookup response must be a list"
            )
        if not markets:
            return None
        market = next(
            (
                candidate
                for candidate in markets
                if isinstance(candidate, dict)
                and candidate.get("conditionId") == condition_id
            ),
            None,
        )
        if market is None:
            returned_ids = [
                candidate.get("conditionId")
                for candidate in markets
                if isinstance(candidate, dict)
            ]
            raise GammaConditionMismatchError(
                "Gamma condition lookup returned no exact match for "
                f"{condition_id}; returned={returned_ids}"
            )
        market = self._parse_market(market)
        # A condition lookup is the independent raw follow-up source. Keep the
        # local receipt clock even for closed or now-illiquid markets that are
        # absent from the filtered research sweep.
        market["_gammaObservedAt"] = datetime.now(timezone.utc).isoformat()
        return market

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
