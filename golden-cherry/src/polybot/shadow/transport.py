"""Budget-aware credential-free GET transport with attempt evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

import requests

from .config import TransportSettings, canonical_json


def iso_utc(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class CollectionBudgetExceeded(RuntimeError):
    """Raised before an operation would cross the cooperative run deadline."""


class PublicRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.status_code = status_code


class CollectionDeadline:
    def __init__(
        self,
        budget_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(budget_seconds) or not 0 < budget_seconds < 300:
            raise ValueError("collection budget must be finite and below five minutes")
        self.budget_seconds = float(budget_seconds)
        self._monotonic = monotonic
        self.started = monotonic()

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._monotonic() - self.started)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.budget_seconds - self.elapsed_seconds)

    def require(self, minimum_seconds: float = 0.05) -> float:
        elapsed = self.elapsed_seconds
        remaining = max(0.0, self.budget_seconds - elapsed)
        if remaining < minimum_seconds:
            raise CollectionBudgetExceeded(
                f"collection budget exhausted after {elapsed:.3f}s"
            )
        return remaining


@dataclass(frozen=True)
class PublicJsonResponse:
    payload: Any
    raw: bytes
    request_id: str
    received_at: str
    response_sha256: str


class PublicGetTransport:
    """GET-only transport; it contains no signer, SDK, or order submission path."""

    def __init__(
        self,
        settings: TransportSettings,
        deadline: CollectionDeadline,
        attempt_sink: Callable[[Mapping[str, Any]], None],
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.deadline = deadline
        self.attempt_sink = attempt_sink
        self.sleep = sleep
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "golden-cherry-shadow-resolution-v2/1.0",
            }
        )

    def get_json(
        self,
        url: str,
        *,
        request_kind: str,
        run_id: str,
        page_number: int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> PublicJsonResponse:
        last_error: BaseException | None = None
        for attempt in range(1, self.settings.max_retries + 2):
            remaining = self.deadline.require()
            request_id = uuid4().hex
            started_at = iso_utc()
            started_clock = time.monotonic()
            response = None
            raw = b""
            try:
                connect_timeout = min(
                    self.settings.connect_timeout_seconds,
                    max(0.05, remaining),
                )
                read_timeout = min(
                    self.settings.read_timeout_seconds,
                    max(0.05, remaining - min(connect_timeout, remaining / 2)),
                )
                response = self.session.get(
                    url,
                    params=dict(params or {}),
                    timeout=(connect_timeout, read_timeout),
                )
                raw = response.content
                response.raise_for_status()
                payload = json.loads(
                    raw.decode("utf-8"),
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(value)
                    ),
                )
                received_at = iso_utc()
                digest = hashlib.sha256(raw).hexdigest()
                self.attempt_sink(
                    {
                        "attempt_id": request_id,
                        "run_id": run_id,
                        "request_kind": request_kind,
                        "page_number": page_number,
                        "attempt_number": attempt,
                        "method": "GET",
                        "url": url,
                        "params_json": canonical_json(dict(params or {})),
                        "started_at": started_at,
                        "completed_at": received_at,
                        "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                        "status": "SUCCESS",
                        "http_status": response.status_code,
                        "response_sha256": digest,
                        "response_bytes": len(raw),
                        "error_type": None,
                        "error_message": None,
                    }
                )
                return PublicJsonResponse(
                    payload, raw, request_id, received_at, digest
                )
            except CollectionBudgetExceeded:
                raise
            except (
                requests.RequestException,
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValueError,
            ) as error:
                last_error = error
                status_code = response.status_code if response is not None else None
                completed_at = iso_utc()
                self.attempt_sink(
                    {
                        "attempt_id": request_id,
                        "run_id": run_id,
                        "request_kind": request_kind,
                        "page_number": page_number,
                        "attempt_number": attempt,
                        "method": "GET",
                        "url": url,
                        "params_json": canonical_json(dict(params or {})),
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                        "status": "FAILED",
                        "http_status": status_code,
                        "response_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
                        "response_bytes": len(raw),
                        "error_type": type(error).__name__,
                        "error_message": " ".join(str(error).splitlines())[:500],
                    }
                )
                retryable = (
                    status_code is None
                    or status_code == 429
                    or (status_code is not None and 500 <= status_code < 600)
                    or isinstance(error, (UnicodeDecodeError, json.JSONDecodeError, ValueError))
                )
                if not retryable or attempt > self.settings.max_retries:
                    raise PublicRequestError(
                        f"public GET failed: {type(error).__name__}",
                        request_id=request_id,
                        status_code=status_code,
                    ) from error
                delay = min(
                    self.settings.retry_max_seconds,
                    self.settings.retry_base_seconds * (2 ** (attempt - 1)),
                )
                if delay:
                    if self.deadline.require() <= delay:
                        raise CollectionBudgetExceeded(
                            "retry delay would exceed collection budget"
                        ) from error
                    self.sleep(delay)
        raise PublicRequestError(
            f"public GET failed: {type(last_error).__name__ if last_error else 'unknown'}"
        )
