"""Bounded sports-only Gamma discovery for Golden Tangerine.

The live A/B jobs deliberately share Golden Black's server-side universe
envelope.  We use ``/events/keyset`` so sports, end-time, liquidity, and
cumulative-volume filters are applied before payloads reach the bot.  Nested
markets are still revalidated locally and an incomplete cursor is fatal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
from typing import Any, Dict, List, Mapping, Optional
from uuid import uuid4

import requests

from ..utils.retry import rate_limit_handler


logger = logging.getLogger(__name__)


class GammaClient:
    BASE_URL = "https://gamma-api.polymarket.com"
    CONNECT_TIMEOUT_SECONDS = 3.05
    READ_TIMEOUT_SECONDS = 30.0
    PAGE_SIZE = 500
    MAX_SWEEP_PAGES = 4
    END_WINDOW_HOURS = 6.0
    SWEEP_SCHEMA_VERSION = 1

    def __init__(self):
        self.session = requests.Session()
        self.sweep_attestations: List[Dict[str, Any]] = []
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "GoldenTangerine-PolyBot/1.0",
            }
        )

    def _get(self, path: str, *, params: Optional[Dict[str, Any]] = None):
        return self.session.get(
            f"{self.BASE_URL}{path}",
            params=params,
            timeout=(self.CONNECT_TIMEOUT_SECONDS, self.READ_TIMEOUT_SECONDS),
        )

    @rate_limit_handler(max_retries=4, base_delay=1.0, retry_forbidden=True)
    def _get_keyset_page(self, params: Dict[str, Any]):
        response = self._get("/events/keyset", params=params)
        response.raise_for_status()
        return response

    @property
    def last_sweep_attestation(self) -> Optional[Dict[str, Any]]:
        return self.sweep_attestations[-1] if self.sweep_attestations else None

    def get_sweep_summaries(self) -> List[Dict[str, Any]]:
        return [
            {key: value for key, value in item.items() if key != "memberships"}
            for item in self.sweep_attestations
        ]

    @staticmethod
    def _parse_json_array(value: Any) -> Optional[list[Any]]:
        if isinstance(value, list):
            return value
        if not isinstance(value, str):
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, list) else None

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0 else None

    @staticmethod
    def _utc(value: Any) -> Optional[datetime]:
        if value in (None, ""):
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @classmethod
    def _sports_evidence(
        cls, event: Mapping[str, Any], market: Mapping[str, Any]
    ) -> bool:
        if any(
            market.get(key) not in (None, "", False)
            for key in ("gameStartTime", "sportsMarketType", "sportsEventId")
        ):
            return True
        words: set[str] = set()
        for source in (event.get("tags"), market.get("tags")):
            for tag in cls._parse_json_array(source) or []:
                if isinstance(tag, Mapping):
                    words.update(
                        str(value).lower()
                        for value in tag.values()
                        if value is not None
                    )
                else:
                    words.add(str(tag).lower())
        words.add(str(event.get("category") or "").lower())
        return any("sport" in word for word in words)

    @classmethod
    def _qualification_reason(
        cls,
        event: Mapping[str, Any],
        market: Mapping[str, Any],
        *,
        observed_at: datetime,
        min_liquidity: float,
        min_volume: float,
    ) -> str:
        condition_id = str(
            market.get("conditionId") or market.get("condition_id") or ""
        ).strip()
        if not condition_id:
            return "missing_condition_id"
        if not cls._sports_evidence(event, market):
            return "sports_revalidation_failed"
        if market.get("active") is not True or market.get("closed") is not False:
            return "inactive_or_closed"
        if market.get("enableOrderBook") is not True:
            return "order_book_disabled"
        if market.get("acceptingOrders") is not True:
            return "orders_not_accepted"
        liquidity = cls._number(
            market.get("liquidityNum", market.get("liquidity"))
        )
        volume = cls._number(market.get("volumeNum", market.get("volume")))
        if liquidity is None or liquidity < min_liquidity:
            return "below_min_liquidity"
        if volume is None or volume < min_volume:
            return "below_min_volume"
        end_date = cls._utc(market.get("endDate") or event.get("endDate"))
        if end_date is None:
            return "missing_end_date"
        hours_left = (end_date - observed_at).total_seconds() / 3600.0
        if not 0 < hours_left <= cls.END_WINDOW_HOURS:
            return "outside_end_window"
        outcomes = cls._parse_json_array(market.get("outcomes"))
        tokens = cls._parse_json_array(
            market.get("clobTokenIds") or market.get("clob_token_ids")
        )
        prices = cls._parse_json_array(
            market.get("outcomePrices") or market.get("outcome_prices")
        )
        normalized_outcomes = (
            [str(outcome or "").strip() for outcome in outcomes]
            if outcomes is not None
            else []
        )
        normalized_tokens = (
            [str(token or "").strip() for token in tokens]
            if tokens is not None
            else []
        )
        normalized_prices = (
            [cls._number(price) for price in prices]
            if prices is not None
            else []
        )
        if (
            len(normalized_outcomes) != 2
            or any(not outcome for outcome in normalized_outcomes)
            or len(set(normalized_outcomes)) != 2
            or len(normalized_tokens) != 2
            or any(not token for token in normalized_tokens)
            or len(set(normalized_tokens)) != 2
            or len(normalized_prices) != 2
            or any(
                price is None or price < 0 or price > 1
                for price in normalized_prices
            )
            or not isinstance(market.get("negRisk"), bool)
        ):
            return "not_aligned_two_outcome"
        return "qualified"

    @staticmethod
    def _enrich_market(
        event: Mapping[str, Any], market: Mapping[str, Any]
    ) -> Dict[str, Any]:
        result = dict(market)
        if not result.get("endDate") and event.get("endDate"):
            result["endDate"] = event.get("endDate")
        if not result.get("tags") and event.get("tags"):
            result["tags"] = event.get("tags")
        result["events"] = [
            {
                "id": event.get("id"),
                "slug": event.get("slug"),
                "title": event.get("title"),
                "tags": event.get("tags") or [],
            }
        ]
        return result

    def get_all_tradable_markets(
        self,
        min_liquidity: float = 0,
        min_volume: float = 0,
    ) -> List[Dict[str, Any]]:
        """Return a terminal-cursor sports/end-date universe.

        ``min_volume`` is Gamma cumulative volume, not 24-hour volume.
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

        observed_at = datetime.now(timezone.utc)
        started_at = observed_at
        sweep_id = str(uuid4())
        after_cursor: Optional[str] = None
        seen_cursors: set[str] = set()
        raw_market_count = 0
        event_count = 0
        pages = 0
        memberships: Dict[str, Dict[str, Any]] = {}
        qualified: Dict[str, Dict[str, Any]] = {}

        for page_number in range(1, self.MAX_SWEEP_PAGES + 1):
            params: Dict[str, Any] = {
                "limit": self.PAGE_SIZE,
                "closed": "false",
                "tag_slug": "sports",
                "liquidity_min": min_liquidity,
                "volume_min": min_volume,
                "end_date_min": self._iso(observed_at),
                "end_date_max": self._iso(
                    observed_at + timedelta(hours=self.END_WINDOW_HOURS)
                ),
            }
            if after_cursor:
                params["after_cursor"] = after_cursor
            response = self._get_keyset_page(params)
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise ValueError("Gamma event keyset response must be an object")
            events = payload.get("events")
            if not isinstance(events, list) or any(
                not isinstance(event, Mapping) for event in events
            ):
                raise ValueError("Gamma event keyset events must be objects")
            pages = page_number
            event_count += len(events)
            for event in events:
                markets = event.get("markets")
                if not isinstance(markets, list):
                    continue
                for raw_market in markets:
                    if not isinstance(raw_market, Mapping):
                        continue
                    raw_market_count += 1
                    market = self._enrich_market(event, raw_market)
                    condition_id = str(
                        market.get("conditionId")
                        or market.get("condition_id")
                        or ""
                    ).strip()
                    if not condition_id:
                        continue
                    item = memberships.setdefault(
                        condition_id,
                        {
                            "condition_id": condition_id,
                            "raw_seen_count": 0,
                            "qualified": False,
                            "qualification_reason": None,
                        },
                    )
                    item["raw_seen_count"] += 1
                    reason = self._qualification_reason(
                        event,
                        market,
                        observed_at=observed_at,
                        min_liquidity=min_liquidity,
                        min_volume=min_volume,
                    )
                    if reason == "qualified":
                        item["qualified"] = True
                        item["qualification_reason"] = "qualified"
                        qualified[condition_id] = market
                    elif not item["qualified"]:
                        item["qualification_reason"] = reason

            next_cursor_raw = payload.get("next_cursor")
            next_cursor = str(next_cursor_raw) if next_cursor_raw else None
            if next_cursor is None:
                break
            if next_cursor == after_cursor or next_cursor in seen_cursors:
                raise RuntimeError("Gamma event keyset cursor did not advance")
            if page_number == self.MAX_SWEEP_PAGES:
                raise RuntimeError("Gamma event keyset exceeded the four-page cap")
            seen_cursors.add(next_cursor)
            after_cursor = next_cursor
        else:  # pragma: no cover - loop always breaks or raises
            raise RuntimeError("Gamma event keyset did not terminate")

        ordered_memberships = sorted(
            memberships.values(), key=lambda item: item["condition_id"]
        )
        digest_payload = [
            item for item in ordered_memberships if item["qualified"]
        ]
        digest = hashlib.sha256(
            json.dumps(
                digest_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        exclusion_counts: Dict[str, int] = {}
        for item in ordered_memberships:
            if item["qualified"]:
                continue
            reason = str(item["qualification_reason"] or "unknown")
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
        attestation = {
            "schema_version": self.SWEEP_SCHEMA_VERSION,
            "sweep_id": sweep_id,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "cursor_complete": True,
            "pages": pages,
            "event_count": event_count,
            "raw_market_count": raw_market_count,
            "unique_condition_count": len(ordered_memberships),
            "qualified_market_count": len(qualified),
            "excluded_condition_count": len(ordered_memberships) - len(qualified),
            "exclusion_counts": dict(sorted(exclusion_counts.items())),
            "missing_condition_id_count": raw_market_count
            - sum(int(item["raw_seen_count"]) for item in ordered_memberships),
            "duplicate_raw_count": sum(
                max(0, int(item["raw_seen_count"]) - 1)
                for item in ordered_memberships
            ),
            "min_liquidity": min_liquidity,
            "min_volume": min_volume,
            "membership_digest_sha256": digest,
            "membership_digest_scope": "qualified_only",
            "memberships": ordered_memberships,
            "request_endpoint": "/events/keyset",
            "tag_slug": "sports",
            "end_window_hours": self.END_WINDOW_HOURS,
        }
        self.sweep_attestations.append(attestation)
        logger.info(
            "Golden Tangerine sports sweep - events=%s markets=%s eligible=%s pages=%s",
            event_count,
            raw_market_count,
            len(qualified),
            pages,
        )
        return list(qualified.values())

    def get_market_by_condition_id(
        self, condition_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            response = self._get(
                "/markets", params={"condition_ids": condition_id, "limit": 1}
            )
            response.raise_for_status()
            markets = response.json()
            return dict(markets[0]) if isinstance(markets, list) and markets else None
        except requests.exceptions.RequestException as error:
            logger.warning(
                "Gamma market lookup failed - condition=%s error=%s",
                condition_id,
                type(error).__name__,
            )
            return None

    def get_event_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self._get(f"/events/{event_id}")
            response.raise_for_status()
            payload = response.json()
            return dict(payload) if isinstance(payload, Mapping) else None
        except requests.exceptions.RequestException as error:
            logger.warning(
                "Gamma event lookup failed - event=%s error=%s",
                event_id,
                type(error).__name__,
            )
            return None

    @staticmethod
    def get_market_tags(market: Dict[str, Any]) -> List[str]:
        tags = market.get("tags") or []
        return [
            str(tag.get("slug") or "") if isinstance(tag, Mapping) else str(tag)
            for tag in tags
        ] if isinstance(tags, list) else []
