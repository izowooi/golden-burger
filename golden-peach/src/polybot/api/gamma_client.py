"""Cursor-complete, server-filtered in-play sports discovery."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from typing import Any, Dict, List, Mapping, Optional
from uuid import uuid4

import requests

from ..config import (
    SPORT_FAMILY_MAX_IN_PLAY_HOURS,
    SPORT_FAMILY_TAG_IDS,
    SPORT_PARAMETER_PROFILES,
)
from ..league_classifier import classify_sports_event
from ..strategy.filters import match_result_reason
from ..utils.deadline import CycleBudget
from ..utils.retry import rate_limit_handler


logger = logging.getLogger(__name__)


class GammaClient:
    BASE_URL = "https://gamma-api.polymarket.com"
    # The next one-minute Jenkins run is the retry. A live sweep therefore uses
    # one fail-fast request per page and never honors a 60-second Retry-After in
    # process. Four capped pages have a 28-second socket-timeout envelope.
    CONNECT_TIMEOUT_SECONDS = 2.0
    READ_TIMEOUT_SECONDS = 5.0
    PAGE_SIZE = 500
    MAX_SWEEP_PAGES = 4
    DIRECT_SPORT_MAX_SWEEP_PAGES = 2
    SWEEP_SCHEMA_VERSION = 2

    def __init__(
        self,
        *,
        sport_family: str = "soccer",
        sport_profile_version: Optional[str] = None,
        cycle_budget: Optional[CycleBudget] = None,
    ):
        normalized_family = str(sport_family or "").strip().lower()
        if normalized_family not in SPORT_FAMILY_TAG_IDS:
            raise ValueError(
                f"unsupported Golden Peach sport family: "
                f"{normalized_family or '<empty>'}"
            )
        self.sport_family = normalized_family
        if sport_profile_version is None:
            self.sport_profile = SPORT_PARAMETER_PROFILES[normalized_family]
        else:
            matching_profiles = tuple(
                profile
                for profile in SPORT_PARAMETER_PROFILES.values()
                if profile.code == normalized_family
                and profile.profile_version == sport_profile_version
            )
            if len(matching_profiles) != 1:
                raise ValueError(
                    "Golden Peach sport profile version does not uniquely match "
                    f"{normalized_family}: {sport_profile_version}"
                )
            self.sport_profile = matching_profiles[0]
        self.tag_id = SPORT_FAMILY_TAG_IDS[normalized_family]
        self.max_in_play_hours = SPORT_FAMILY_MAX_IN_PLAY_HOURS[normalized_family]
        self.cycle_budget = cycle_budget
        self.session = requests.Session()
        self.sweep_attestations: List[Dict[str, Any]] = []
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "GoldenPeach-PolyBot/1.0",
            }
        )

    def close(self) -> None:
        """Release the per-cycle keep-alive pool after all evidence is durable."""
        self.session.close()

    def _get(self, path: str, *, params: Optional[Dict[str, Any]] = None):
        timeout = (self.CONNECT_TIMEOUT_SECONDS, self.READ_TIMEOUT_SECONDS)
        if self.cycle_budget is not None:
            timeout = self.cycle_budget.request_timeouts(
                self.CONNECT_TIMEOUT_SECONDS,
                self.READ_TIMEOUT_SECONDS,
                context=f"Gamma {path}",
            )
        return self.session.get(
            f"{self.BASE_URL}{path}",
            params=params,
            timeout=timeout,
        )

    @rate_limit_handler(max_retries=1, base_delay=1.0, retry_forbidden=True)
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

    def _qualification_reason(
        self,
        event: Mapping[str, Any],
        market: Mapping[str, Any],
        *,
        observed_at: datetime,
    ) -> str:
        condition_id = str(
            market.get("conditionId") or market.get("condition_id") or ""
        ).strip()
        if not condition_id:
            return "missing_condition_id"
        classification = classify_sports_event(event, self.sport_family)
        if not classification.accepted:
            base_reason = (
                str(classification.reasons[0]).lower()
                if classification.reasons
                else "league_not_allowed"
            )
            # Keep a bounded, normalized sport identity in the exclusion
            # bucket.  A bare ``league_not_allowed`` count cannot distinguish
            # a legitimately quiet elite-soccer window from a source/classifier
            # drift that accidentally rejects EPL (or another frozen league).
            raw_sport_code = str(
                classification.evidence.get("sport_code") or "missing"
            ).strip().casefold()
            sport_code = "".join(
                character
                for character in raw_sport_code[:40]
                if character.isalnum() or character in {"-", "_"}
            ) or "missing"
            return (
                f"{base_reason}:sport={sport_code}:"
                f"status={classification.status.casefold()}"
            )
        if event.get("parentEventId") not in (None, ""):
            return "child_event_not_whole_match"
        if event.get("active") is not True or event.get("closed") is not False:
            return "event_inactive_or_closed"
        if event.get("live") is not True or event.get("ended") is not False:
            return "event_not_explicitly_in_play"
        game_start = self._utc(
            market.get("gameStartTime")
            or event.get("startTime")
            or event.get("eventDate")
            or event.get("startDate")
            or event.get("eventStartTime")
            or event.get("gameStartTime")
        )
        if game_start is None:
            return "game_start_time_missing"
        in_play_hours = (observed_at - game_start).total_seconds() / 3600.0
        if not 0 <= in_play_hours <= self.max_in_play_hours:
            return "outside_in_play_window"
        if market.get("active") is not True or market.get("closed") is not False:
            return "market_inactive_or_closed"
        if market.get("enableOrderBook") is not True:
            return "order_book_disabled"
        if market.get("acceptingOrders") is not True:
            return "orders_not_accepted"
        result_reason, _ = match_result_reason(self._enrich_market(event, market))
        if result_reason != "ok":
            return result_reason
        return "qualified"

    def _enrich_market(
        self, event: Mapping[str, Any], market: Mapping[str, Any]
    ) -> Dict[str, Any]:
        result = dict(market)
        if not result.get("endDate") and event.get("endDate"):
            result["endDate"] = event.get("endDate")
        if not result.get("tags") and event.get("tags"):
            result["tags"] = event.get("tags")
        classification = classify_sports_event(event, self.sport_family)
        result["sportFamily"] = self.sport_family
        result["leagueCode"] = classification.league_code
        result["leagueName"] = classification.league_name
        result["leagueClassifierStatus"] = classification.status
        result["leagueClassifierVersion"] = classification.evidence.get(
            "classifier_version"
        )
        result["leagueMappingSha256"] = classification.evidence.get(
            "league_mapping_sha256"
        )
        result["events"] = [
            {
                "id": event.get("id"),
                "slug": event.get("slug"),
                "title": event.get("title"),
                "parentEventId": event.get("parentEventId"),
                "active": event.get("active"),
                "closed": event.get("closed"),
                "live": event.get("live"),
                "ended": event.get("ended"),
                "startTime": event.get("startTime"),
                "eventDate": event.get("eventDate"),
                "endDate": event.get("endDate"),
                "score": event.get("score"),
                "elapsed": event.get("elapsed"),
                "period": event.get("period"),
                "sport": event.get("sport") or {},
                "tags": event.get("tags") or [],
                "series": event.get("series") or [],
                "seriesSlug": event.get("seriesSlug"),
                "teams": event.get("teams") or [],
            }
        ]
        return result

    def get_all_tradable_markets(
        self,
        min_liquidity: float = 0,
        min_volume: float = 0,
    ) -> List[Dict[str, Any]]:
        """Return cursor-complete in-play whole-match result propositions.

        Gamma applies cumulative volume/liquidity bounds before returning an
        event. Exact executable `$5` CLOB depth is the final, stricter gate.
        """
        if (
            not math.isfinite(float(min_liquidity))
            or not math.isfinite(float(min_volume))
            or float(min_liquidity) < 0
            or float(min_volume) < 0
        ):
            raise ValueError("server liquidity and volume gates must be nonnegative")

        observed_at = datetime.now(timezone.utc)
        sweep_id = str(uuid4())
        after_cursor: Optional[str] = None
        seen_cursors: set[str] = set()
        raw_market_count = 0
        event_count = 0
        pages = 0
        memberships: Dict[str, Dict[str, Any]] = {}
        qualified: Dict[str, Dict[str, Any]] = {}

        page_number = 0
        max_sweep_pages = min(
            self.sport_profile.max_sweep_pages,
            self.MAX_SWEEP_PAGES
            if self.sport_family == "soccer"
            else self.DIRECT_SPORT_MAX_SWEEP_PAGES,
        )
        while page_number < max_sweep_pages:
            page_number += 1
            params: Dict[str, Any] = {
                "limit": self.PAGE_SIZE,
                "closed": "false",
                "live": "true",
                "tag_id": self.tag_id,
                "related_tags": "false",
                "liquidity_min": float(min_liquidity),
                "volume_min": float(min_volume),
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
                        raw_market,
                        observed_at=observed_at,
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
            if page_number == max_sweep_pages:
                raise RuntimeError(
                    "Gamma event keyset exceeded the configured page cap"
                )
            seen_cursors.add(next_cursor)
            after_cursor = next_cursor
        ordered_memberships = sorted(
            memberships.values(), key=lambda item: item["condition_id"]
        )
        digest_payload = [item for item in ordered_memberships if item["qualified"]]
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
            "started_at": observed_at.isoformat(),
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
            "min_liquidity": float(min_liquidity),
            "min_volume": float(min_volume),
            "membership_digest_sha256": digest,
            "membership_digest_scope": "qualified_only",
            "memberships": ordered_memberships,
            "request_endpoint": "/events/keyset",
            "tag_id": self.tag_id,
            "sport_family": self.sport_family,
            "sport_profile_version": self.sport_profile.profile_version,
            "book_shape": self.sport_profile.book_shape,
            "max_sweep_pages": max_sweep_pages,
            "related_tags": False,
            "live_only": True,
            "max_in_play_hours": self.max_in_play_hours,
        }
        self.sweep_attestations.append(attestation)
        logger.info(
            "Golden Peach %s sweep - events=%s markets=%s "
            "eligible_result_markets=%s pages=%s",
            self.sport_family,
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
            if not isinstance(markets, list) or not markets:
                return None
            market = dict(markets[0])
            market["sportFamily"] = self.sport_family
            return market
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
