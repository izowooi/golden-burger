"""Complete Gamma census and bounded closed-market resolution lookups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..config import GammaConfig
from ..utils.retry import PublicApiError, PublicJsonTransport, iso_utc


@dataclass(frozen=True)
class GammaPage:
    page_number: int
    cursor_in: str | None
    cursor_out: str | None
    request_id: str
    request_hash: str
    received_at: str
    response_sha256: str
    raw: bytes
    markets: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class GammaSweep:
    started_at: str
    completed_at: str
    pages: tuple[GammaPage, ...]
    cursor_complete: bool

    @property
    def markets(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in self.pages:
            for item_number, market in enumerate(page.markets):
                rows.append(
                    {
                        **market,
                        "_page_number": page.page_number,
                        "_item_number": item_number,
                        "_page_received_at": page.received_at,
                        "_page_request_id": page.request_id,
                    }
                )
        return rows


@dataclass(frozen=True)
class ResolutionLookup:
    condition_id: str
    lookup_status: str
    requested_at: str
    observed_at: str
    request_id: str | None
    response_sha256: str | None
    raw: bytes | None
    market: dict[str, Any] | None
    error_type: str | None = None
    error_message: str | None = None


class GammaClient:
    KEYSET_ENDPOINT = "/markets/keyset"
    RESOLUTION_ENDPOINT = "/markets"

    def __init__(self, config: GammaConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    def collect_market_sweep(self, run_id: str) -> GammaSweep:
        started_at = iso_utc()
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages: list[GammaPage] = []
        for page_number in range(1, self.config.max_pages + 1):
            params: dict[str, Any] = {
                "closed": "false",
                "include_tag": "true" if self.config.include_tags else "false",
                "limit": self.config.page_size,
                "liquidity_num_min": self.config.min_liquidity,
                "volume_num_min": self.config.min_total_volume,
            }
            if cursor is not None:
                params["after_cursor"] = cursor
            response = self.transport.request_json(
                "GET",
                f"{self.config.base_url}{self.KEYSET_ENDPOINT}",
                request_kind="gamma_markets_keyset",
                run_id=run_id,
                page_number=page_number,
                params=params,
            )
            payload = response.payload
            if not isinstance(payload, Mapping):
                raise ValueError("Gamma keyset page must be an object")
            markets_raw = payload.get("markets")
            if not isinstance(markets_raw, list) or any(
                not isinstance(item, Mapping) for item in markets_raw
            ):
                raise ValueError("Gamma keyset markets must be a list of objects")
            cursor_raw = payload.get("next_cursor")
            if cursor_raw is not None and not isinstance(cursor_raw, str):
                raise ValueError("Gamma next_cursor must be a string or null")
            next_cursor = cursor_raw.strip() if isinstance(cursor_raw, str) else None
            if next_cursor == "":
                next_cursor = None
            pages.append(
                GammaPage(
                    page_number=page_number,
                    cursor_in=cursor,
                    cursor_out=next_cursor,
                    request_id=response.request_id,
                    request_hash=response.request_hash,
                    received_at=response.received_at,
                    response_sha256=response.response_sha256,
                    raw=response.raw,
                    markets=tuple(dict(item) for item in markets_raw),
                )
            )
            if next_cursor is None:
                return GammaSweep(
                    started_at=started_at,
                    completed_at=iso_utc(),
                    pages=tuple(pages),
                    cursor_complete=True,
                )
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise RuntimeError("Gamma keyset returned a repeated cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise RuntimeError("Gamma keyset exceeded max_pages before terminal cursor")

    def fetch_resolutions(
        self,
        run_id: str,
        condition_ids: Sequence[str],
    ) -> list[ResolutionLookup]:
        unique = list(
            dict.fromkeys(str(value) for value in condition_ids if str(value))
        )
        rows: list[ResolutionLookup] = []
        for offset in range(0, len(unique), self.config.resolution_batch_size):
            chunk = unique[offset : offset + self.config.resolution_batch_size]
            requested_at = iso_utc()
            response = None
            try:
                response = self.transport.request_json(
                    "GET",
                    f"{self.config.base_url}{self.RESOLUTION_ENDPOINT}",
                    request_kind="gamma_resolution_lookup",
                    run_id=run_id,
                    page_number=offset // self.config.resolution_batch_size + 1,
                    params={
                        "condition_ids": chunk,
                        "closed": "true",
                        "limit": len(chunk),
                    },
                )
                payload = response.payload
                markets_raw = (
                    payload.get("markets") if isinstance(payload, Mapping) else payload
                )
                if not isinstance(markets_raw, list) or any(
                    not isinstance(item, Mapping) for item in markets_raw
                ):
                    raise ValueError(
                        "Gamma resolution payload must contain market objects"
                    )
                by_condition: dict[str, dict[str, Any]] = {}
                for item in markets_raw:
                    condition_id = str(
                        item.get("conditionId") or item.get("condition_id") or ""
                    )
                    if condition_id and condition_id not in by_condition:
                        by_condition[condition_id] = dict(item)
                for condition_id in chunk:
                    market = by_condition.get(condition_id)
                    rows.append(
                        ResolutionLookup(
                            condition_id=condition_id,
                            lookup_status="OBSERVED"
                            if market is not None
                            else "MISSING",
                            requested_at=requested_at,
                            observed_at=response.received_at,
                            request_id=response.request_id,
                            response_sha256=response.response_sha256,
                            raw=response.raw,
                            market=market,
                        )
                    )
            except (PublicApiError, ValueError) as error:
                status = "MALFORMED" if isinstance(error, ValueError) else "ERROR"
                for condition_id in chunk:
                    rows.append(
                        ResolutionLookup(
                            condition_id=condition_id,
                            lookup_status=status,
                            requested_at=requested_at,
                            observed_at=(
                                response.received_at if response else iso_utc()
                            ),
                            request_id=response.request_id if response else None,
                            response_sha256=(
                                response.response_sha256 if response else None
                            ),
                            raw=response.raw if response else None,
                            market=None,
                            error_type=type(error).__name__,
                            error_message=" ".join(str(error).splitlines())[:500],
                        )
                    )
        return rows


__all__ = [
    "GammaClient",
    "GammaPage",
    "GammaSweep",
    "ResolutionLookup",
]
