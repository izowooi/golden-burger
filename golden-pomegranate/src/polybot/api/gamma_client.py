"""Cursor-complete Gamma census and bounded resolution lookups."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

import requests
from requests.exceptions import RequestException

from ..config import GammaConfig
from ..utils.retry import canonical_json, get_json_with_retry, utc_now


@dataclass(frozen=True)
class GammaPage:
    page_number: int
    cursor_requested: str | None
    next_cursor: str | None
    received_at: str
    request_id: str
    request_hash: str
    raw_payload_id: str | None
    markets: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class GammaSweep:
    sweep_id: str
    cycle_number: int
    started_at: str
    completed_at: str
    pages: tuple[GammaPage, ...]
    request_attestation_json: str
    request_attestation_sha256: str

    @property
    def markets(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in self.pages:
            for item_number, raw in enumerate(page.markets):
                result.append(
                    {
                        **dict(raw),
                        "_page_number": page.page_number,
                        "_item_number": item_number,
                        "_page_received_at": page.received_at,
                        "_page_request_id": page.request_id,
                    }
                )
        return result


class GammaClient:
    """Credential-free Gamma reader that publishes only complete keyset sweeps."""

    def __init__(
        self,
        config: GammaConfig | None = None,
        *,
        evidence_sink: Callable[[Mapping[str, Any]], None] | None = None,
        raw_payload_sink: Callable[..., str] | None = None,
        session: requests.Session | None = None,
        raw_payload_every_cycles: int = 1,
    ) -> None:
        self.config = config or GammaConfig()
        self.evidence_sink = evidence_sink
        self.raw_payload_sink = raw_payload_sink
        self.raw_payload_every_cycles = raw_payload_every_cycles
        self.session = session or requests.Session()
        # ``requests`` otherwise consults proxy variables and ``.netrc`` and can
        # silently turn this public client into an authenticated one.
        self.session.trust_env = False
        if hasattr(self.session, "headers"):
            self.session.headers.update(
                {
                    "Accept": "application/json",
                    "User-Agent": "GoldenPomegranate-Research/1.0",
                }
            )

    def _get(
        self,
        path: str,
        *,
        params: Mapping[str, Any],
        run_id: str | None,
        sweep_attempt_id: str,
        request_kind: str,
        page_number: int,
    ):
        return get_json_with_retry(
            self.session,
            f"{self.config.base_url}{path}",
            params=params,
            timeout=(
                self.config.connect_timeout_seconds,
                self.config.read_timeout_seconds,
            ),
            max_attempts=self.config.max_retries,
            base_delay_seconds=self.config.retry_base_seconds,
            max_delay_seconds=self.config.retry_max_seconds,
            evidence_sink=self.evidence_sink,
            run_id=run_id,
            sweep_attempt_id=sweep_attempt_id,
            request_kind=request_kind,
            page_number=page_number,
        )

    def _store_raw(
        self,
        *,
        request_id: str,
        kind: str,
        content: bytes,
        cycle_number: int,
    ) -> str | None:
        if self.raw_payload_sink is None:
            return None
        store_blob = (cycle_number - 1) % self.raw_payload_every_cycles == 0
        return self.raw_payload_sink(
            request_id=request_id,
            kind=kind,
            content=content,
            store_blob=store_blob,
        )

    def fetch_complete_sweep(
        self,
        sweep_id: str | None = None,
        cycle_number: int = 1,
        run_id: str | None = None,
    ) -> GammaSweep:
        """Traverse ``/markets/keyset`` completely with zero server minima.

        No active/orderbook/liquidity/strategy filter is applied to returned
        rows. ``closed=false`` is the sole lifecycle envelope requested by the
        research contract, and every raw market in it is published.
        """
        if cycle_number <= 0:
            raise ValueError("cycle_number must be positive")
        sweep_id = sweep_id or str(uuid4())
        started_at = utc_now()
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages: list[GammaPage] = []
        attestations: list[dict[str, Any]] = []

        while True:
            page_number = len(pages) + 1
            if page_number > self.config.max_pages:
                raise RuntimeError("Gamma keyset exceeded configured page safety limit")
            params: dict[str, Any] = {
                "closed": "false",
                "include_tag": "true",
                "limit": self.config.page_size,
            }
            if cursor is not None:
                params["after_cursor"] = cursor
            result = self._get(
                "/markets/keyset",
                params=params,
                run_id=run_id,
                sweep_attempt_id=sweep_id,
                request_kind="gamma_markets_keyset",
                page_number=page_number,
            )
            payload = result.payload
            if not isinstance(payload, Mapping):
                raise ValueError("Gamma keyset payload must be a mapping")
            raw_markets = payload.get("markets")
            if not isinstance(raw_markets, list) or any(
                not isinstance(market, Mapping) for market in raw_markets
            ):
                raise ValueError("Gamma keyset markets must be a list of mappings")
            # Gamma omits ``next_cursor`` on the terminal page. Explicit null
            # and empty values are equivalent terminal representations.
            next_cursor_raw = payload.get("next_cursor")
            next_cursor = (
                str(next_cursor_raw).strip() if next_cursor_raw is not None else None
            )
            if next_cursor == "":
                next_cursor = None
            raw_payload_id = self._store_raw(
                request_id=result.request_id,
                kind="gamma_markets_keyset_page",
                content=result.content,
                cycle_number=cycle_number,
            )
            pages.append(
                GammaPage(
                    page_number=page_number,
                    cursor_requested=cursor,
                    next_cursor=next_cursor,
                    received_at=result.received_at,
                    request_id=result.request_id,
                    request_hash=result.request_hash,
                    raw_payload_id=raw_payload_id,
                    markets=tuple(dict(market) for market in raw_markets),
                )
            )
            attestations.append(
                {
                    "page_number": page_number,
                    "cursor_requested": cursor,
                    "next_cursor": next_cursor,
                    "received_at": result.received_at,
                    "request_id": result.request_id,
                    "request_hash": result.request_hash,
                    "response_count": len(raw_markets),
                    "raw_payload_id": raw_payload_id,
                }
            )
            if next_cursor is None:
                break
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise RuntimeError("Gamma keyset cursor repeated before completion")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        attestation_json = canonical_json(attestations)
        return GammaSweep(
            sweep_id=sweep_id,
            cycle_number=cycle_number,
            started_at=started_at,
            completed_at=utc_now(),
            pages=tuple(pages),
            request_attestation_json=attestation_json,
            request_attestation_sha256=hashlib.sha256(
                attestation_json.encode("utf-8")
            ).hexdigest(),
        )

    def fetch_resolution_batch(
        self,
        condition_ids: Sequence[str],
        *,
        cycle_number: int,
        run_id: str | None,
        sweep_attempt_id: str,
        batch_size: int,
    ) -> list[dict[str, Any]]:
        """Lookup bounded watchlist conditions with explicit missing/error rows."""
        selected = [str(value) for value in condition_ids]
        observations: list[dict[str, Any]] = []
        for offset in range(0, len(selected), batch_size):
            chunk = selected[offset : offset + batch_size]
            page_number = offset // batch_size + 1
            requested_at = utc_now()
            try:
                result = self._get(
                    "/markets",
                    # Gamma declares ``condition_ids`` as an array. Requests
                    # serializes the list as repeated query keys, preserving
                    # the exact public endpoint contract.
                    # The keyset census intentionally excludes closed markets,
                    # while resolution evidence is most often available only
                    # after that transition. Gamma defaults ``closed`` to
                    # false, so the watcher must opt into the closed catalog.
                    params={
                        "condition_ids": chunk,
                        "closed": "true",
                        "limit": len(chunk),
                    },
                    run_id=run_id,
                    sweep_attempt_id=sweep_attempt_id,
                    request_kind="gamma_resolution_lookup",
                    page_number=page_number,
                )
                payload = result.payload
                if isinstance(payload, Mapping):
                    markets = payload.get("markets", [])
                else:
                    markets = payload
                if not isinstance(markets, list) or any(
                    not isinstance(item, Mapping) for item in markets
                ):
                    raise ValueError(
                        "Gamma resolution payload must contain market rows"
                    )
                self._store_raw(
                    request_id=result.request_id,
                    kind="gamma_resolution_lookup",
                    content=result.content,
                    cycle_number=cycle_number,
                )
                by_condition = {
                    str(item.get("conditionId") or item.get("condition_id")): item
                    for item in markets
                    if item.get("conditionId") or item.get("condition_id")
                }
                for condition_id in chunk:
                    market = by_condition.get(condition_id)
                    observations.append(
                        {
                            "condition_id": condition_id,
                            "requested_at": requested_at,
                            "observed_at": result.received_at,
                            "lookup_status": "OBSERVED" if market else "MISSING",
                            "request_id": result.request_id,
                            "raw_market": dict(market) if market else None,
                            "error_type": None,
                            "error_message": None,
                        }
                    )
            except (RequestException, ValueError) as error:
                for condition_id in chunk:
                    observations.append(
                        {
                            "condition_id": condition_id,
                            "requested_at": requested_at,
                            "observed_at": utc_now(),
                            "lookup_status": "ERROR",
                            "request_id": None,
                            "raw_market": None,
                            "error_type": type(error).__name__,
                            "error_message": " ".join(str(error).splitlines())[:500],
                        }
                    )
        return observations


__all__ = ["GammaClient", "GammaPage", "GammaSweep"]
