"""Bounded Gamma metadata and terminal-resolution lookups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..config import GammaConfig
from ..utils.retry import PublicApiError, PublicJsonTransport, iso_utc


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
    MARKET_ENDPOINT = "/markets"

    def __init__(self, config: GammaConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    def _fetch_markets(
        self,
        run_id: str,
        condition_ids: Sequence[str],
        *,
        closed: bool,
        request_kind: str,
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
                    f"{self.config.base_url}{self.MARKET_ENDPOINT}",
                    request_kind=request_kind,
                    run_id=run_id,
                    page_number=offset // self.config.resolution_batch_size + 1,
                    params={
                        "condition_ids": chunk,
                        "closed": "true" if closed else "false",
                        "include_tag": "true",
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
                            request_id=(
                                response.request_id
                                if response
                                else getattr(error, "request_id", None)
                            ),
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

    def fetch_metadata(
        self, run_id: str, condition_ids: Sequence[str]
    ) -> list[ResolutionLookup]:
        return self._fetch_markets(
            run_id,
            condition_ids,
            closed=False,
            request_kind="gamma_candidate_metadata",
        )

    def fetch_resolutions(
        self, run_id: str, condition_ids: Sequence[str]
    ) -> list[ResolutionLookup]:
        return self._fetch_markets(
            run_id,
            condition_ids,
            closed=True,
            request_kind="gamma_resolution_lookup",
        )


__all__ = [
    "GammaClient",
    "ResolutionLookup",
]
