"""Gamma API client for market data retrieval."""
import fcntl
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, List, Dict, Optional
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
    # One shared A/B leader issues at most ten sequential requests/second,
    # comfortably below Gamma's documented /markets limit (300 / 10s).
    KEYSET_PAGE_INTERVAL_SECONDS = 0.1
    SWEEP_SCHEMA_VERSION = 1
    SHARED_CACHE_SCHEMA_VERSION = 1
    SHARED_CACHE_ENV = "POLYBOT_GAMMA_SHARED_CACHE_DIR"
    SHARED_CACHE_BUCKET_SECONDS = 300
    SHARED_CACHE_LOCK_TIMEOUT_SECONDS = 12 * 60
    SHARED_CACHE_MAX_BYTES = 512 * 1024 * 1024

    def __init__(self):
        self.session = requests.Session()
        self.sweep_attestations: List[Dict] = []
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenBlueberry-PolyBot/1.0"
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
    def _membership_digest(memberships: List[Dict]) -> str:
        """Hash the canonical qualified-membership evidence."""
        qualified = sorted(
            (item for item in memberships if item.get("qualified") is True),
            key=lambda item: item["condition_id"],
        )
        encoded = json.dumps(
            qualified,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _shared_cache_root(self) -> Optional[Path]:
        """Return an owner-private, explicitly configured cross-workspace cache."""
        raw = os.getenv(self.SHARED_CACHE_ENV, "").strip()
        if not raw:
            return None
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            raise ValueError(f"{self.SHARED_CACHE_ENV} must be an absolute path")
        if candidate.is_symlink():
            raise RuntimeError("Gamma shared cache directory must not be a symlink")
        candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = candidate.stat()
        if metadata.st_uid != os.getuid():
            raise RuntimeError("Gamma shared cache directory is not owned by this user")
        # The cache contains only public market data, but a private directory
        # prevents another local account from injecting an evidence payload.
        os.chmod(candidate, 0o700)
        return candidate

    def _shared_cache_identity(
        self,
        min_liquidity: float,
        min_volume: float,
        bucket: int,
    ) -> tuple[str, Dict[str, Any]]:
        filters: Dict[str, Any] = {
            "base_url": self.BASE_URL,
            "closed": "false",
            "include_tag": "true",
            "limit": 100,
            "min_liquidity": min_liquidity,
            "min_volume": min_volume,
            "schema_version": self.SHARED_CACHE_SCHEMA_VERSION,
        }
        encoded = json.dumps(
            filters, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        filter_digest = hashlib.sha256(encoded).hexdigest()
        return f"sweep-{bucket}-{filter_digest[:24]}", filters

    def _acquire_shared_cache_lock(self, path: Path):
        """Acquire a bounded, owner-only lock without following a symlink."""
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        handle = os.fdopen(descriptor, "a+")
        deadline = time.monotonic() + self.SHARED_CACHE_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError("Gamma shared cache lock timed out")
                time.sleep(0.25)

    def _validate_cached_sweep(
        self,
        payload: object,
        *,
        cache_key: str,
        bucket: int,
        filters: Dict[str, Any],
    ) -> tuple[List[Dict], Dict]:
        """Validate a completed cache payload before treating it as evidence."""
        if not isinstance(payload, dict):
            raise ValueError("Gamma shared cache payload must be an object")
        if payload.get("schema_version") != self.SHARED_CACHE_SCHEMA_VERSION:
            raise ValueError("Gamma shared cache schema mismatch")
        if payload.get("cache_key") != cache_key or payload.get("bucket") != bucket:
            raise ValueError("Gamma shared cache identity mismatch")
        if payload.get("filters") != filters:
            raise ValueError("Gamma shared cache filters mismatch")
        markets = payload.get("markets")
        attestation = payload.get("attestation")
        if not isinstance(markets, list) or any(
            not isinstance(market, dict) for market in markets
        ):
            raise ValueError("Gamma shared cache markets are invalid")
        if not isinstance(attestation, dict):
            raise ValueError("Gamma shared cache attestation is invalid")
        if attestation.get("cursor_complete") is not True:
            raise ValueError("Gamma shared cache is not cursor-complete")
        memberships = attestation.get("memberships")
        if not isinstance(memberships, list) or any(
            not isinstance(item, dict) for item in memberships
        ):
            raise ValueError("Gamma shared cache memberships are invalid")
        expected_digest = self._membership_digest(memberships)
        if attestation.get("membership_digest_sha256") != expected_digest:
            raise ValueError("Gamma shared cache membership digest mismatch")
        qualified_ids = {
            str(item.get("condition_id") or "")
            for item in memberships
            if item.get("qualified") is True
        }
        market_ids = [str(market.get("conditionId") or "") for market in markets]
        if (
            "" in qualified_ids
            or "" in market_ids
            or len(market_ids) != len(set(market_ids))
            or set(market_ids) != qualified_ids
        ):
            raise ValueError("Gamma shared cache market membership mismatch")
        if int(attestation.get("qualified_market_count", -1)) != len(markets):
            raise ValueError("Gamma shared cache qualified count mismatch")
        return markets, attestation

    def _read_shared_cache(
        self,
        path: Path,
        *,
        cache_key: str,
        bucket: int,
        filters: Dict[str, Any],
    ) -> Optional[tuple[List[Dict], Dict]]:
        if not path.is_file():
            return None
        if path.is_symlink() or path.stat().st_size > self.SHARED_CACHE_MAX_BYTES:
            raise ValueError("Gamma shared cache file is unsafe or too large")
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return self._validate_cached_sweep(
            payload,
            cache_key=cache_key,
            bucket=bucket,
            filters=filters,
        )

    def _write_shared_cache(
        self,
        path: Path,
        *,
        cache_key: str,
        bucket: int,
        filters: Dict[str, Any],
        markets: List[Dict],
        attestation: Dict,
    ) -> None:
        payload = {
            "schema_version": self.SHARED_CACHE_SCHEMA_VERSION,
            "cache_key": cache_key,
            "bucket": bucket,
            "filters": filters,
            "markets": markets,
            "attestation": attestation,
        }
        temporary_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{cache_key}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_name = handle.name
                os.chmod(temporary_name, 0o600)
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
            os.chmod(path, 0o600)
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def _prune_shared_cache(root: Path, current_path: Path) -> None:
        candidates = sorted(
            (
                candidate
                for candidate in root.glob("sweep-*.json")
                if candidate.is_file() and not candidate.is_symlink()
            ),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )
        keep = {current_path, *candidates[:3]}
        for candidate in candidates:
            if candidate in keep:
                continue
            try:
                candidate.unlink()
            except FileNotFoundError:
                continue

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

    def _matching_market(
        self,
        payload: object,
        condition_id: str,
        *,
        require_closed: bool = False,
    ) -> Optional[Dict]:
        """Return only an identity-matched market from a Gamma list response."""
        if not isinstance(payload, list):
            logger.warning(
                "시장 조회 응답 형식 불일치 - condition: %s payload_type: %s",
                condition_id,
                type(payload).__name__,
            )
            return None

        expected_condition_id = condition_id.casefold()
        for raw_market in payload:
            if not isinstance(raw_market, dict):
                continue
            actual_condition_id = str(raw_market.get("conditionId") or "").strip()
            if actual_condition_id.casefold() != expected_condition_id:
                continue
            if require_closed and raw_market.get("closed") is not True:
                continue
            return self._parse_market(raw_market)
        return None

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
        cache_root = self._shared_cache_root()
        if cache_root is None:
            return self._get_all_tradable_markets_uncached(
                min_liquidity=min_liquidity,
                min_volume=min_volume,
            )

        bucket = int(time.time()) // self.SHARED_CACHE_BUCKET_SECONDS
        cache_key, filters = self._shared_cache_identity(
            min_liquidity,
            min_volume,
            bucket,
        )
        cache_path = cache_root / f"{cache_key}.json"
        lock_path = cache_root / f"{cache_key}.lock"
        logger.info("공유 Gamma sweep 대기 - bucket=%s", bucket)
        lock = self._acquire_shared_cache_lock(lock_path)
        try:
            try:
                cached = self._read_shared_cache(
                    cache_path,
                    cache_key=cache_key,
                    bucket=bucket,
                    filters=filters,
                )
            except (OSError, ValueError, json.JSONDecodeError) as error:
                logger.warning("무효 Gamma 공유 캐시를 폐기합니다 - %s", error)
                try:
                    cache_path.unlink()
                except FileNotFoundError:
                    pass
                cached = None
            if cached is not None:
                markets, source_attestation = cached
                attestation = dict(source_attestation)
                source_sweep_id = str(
                    attestation.get("source_sweep_id")
                    or attestation.get("sweep_id")
                    or ""
                )
                if not source_sweep_id:
                    raise ValueError("Gamma shared cache source sweep is missing")
                attestation.update(
                    {
                        "sweep_id": str(uuid4()),
                        "source_sweep_id": source_sweep_id,
                        "shared_cache_hit": True,
                        "shared_cache_bucket": bucket,
                    }
                )
                self.sweep_attestations.append(attestation)
                logger.info(
                    "공유 Gamma sweep 적중 - 시장 %s개, keyset %s페이지, "
                    "source=%s",
                    len(markets),
                    attestation.get("pages"),
                    source_sweep_id,
                )
                return [self._parse_market(market) for market in markets]

            markets = self._get_all_tradable_markets_uncached(
                min_liquidity=min_liquidity,
                min_volume=min_volume,
            )
            attestation = self.last_sweep_attestation
            if attestation is None or attestation.get("cursor_complete") is not True:
                raise RuntimeError("completed Gamma sweep required before cache publish")
            source_sweep_id = str(attestation["sweep_id"])
            attestation.update(
                {
                    "source_sweep_id": source_sweep_id,
                    "shared_cache_hit": False,
                    "shared_cache_bucket": bucket,
                }
            )
            self._write_shared_cache(
                cache_path,
                cache_key=cache_key,
                bucket=bucket,
                filters=filters,
                markets=markets,
                attestation=attestation,
            )
            self._prune_shared_cache(cache_root, cache_path)
            logger.info(
                "공유 Gamma sweep 발행 - 시장 %s개, source=%s",
                len(markets),
                source_sweep_id,
            )
            return markets
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def _get_all_tradable_markets_uncached(
        self,
        *,
        min_liquidity: float,
        min_volume: float,
    ) -> List[Dict]:
        """Traverse one complete remote keyset sweep without shared caching."""
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
        exclusion_counts: Dict[str, int] = {}
        for item in sorted_memberships:
            if item["qualified"]:
                continue
            reason = item["qualification_reason"]
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
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
            "membership_digest_sha256": self._membership_digest(
                sorted_memberships
            ),
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
        normalized_condition_id = str(condition_id or "").strip()
        if not normalized_condition_id:
            logger.warning("빈 condition ID로 시장을 조회할 수 없습니다")
            return None

        params = {"condition_ids": normalized_condition_id, "limit": 1}

        try:
            response = self._get("/markets", params=params)
            response.raise_for_status()
            market = self._matching_market(response.json(), normalized_condition_id)
            if market is not None:
                return market

            # Gamma's default listing omits closed markets even when condition_ids
            # is supplied. Retry explicitly so lifecycle management can observe
            # final payout evidence after the CLOB order book disappears.
            closed_params = {**params, "closed": "true"}
            response = self._get("/markets", params=closed_params)
            response.raise_for_status()
            return self._matching_market(
                response.json(),
                normalized_condition_id,
                require_closed=True,
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"시장 조회 실패 - condition: {normalized_condition_id}: {e}")
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
