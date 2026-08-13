"""Audited retries for public JSON endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import time
from typing import Any, Callable
from uuid import uuid4

import requests
from requests import Response
from requests.exceptions import ChunkedEncodingError, RequestException


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    current = value or utc_now()
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class JsonResponse:
    payload: Any
    raw: bytes
    request_id: str
    started_at: str
    received_at: str
    response_sha256: str


class PublicApiError(RuntimeError):
    """Public source failed after bounded retries."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


ReceiptSink = Callable[[dict[str, Any]], None]


def _retry_after(response: Response | None) -> float | None:
    if response is None:
        return None
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


class PublicJsonTransport:
    """A credential-free HTTP transport with one immutable receipt per attempt."""

    def __init__(
        self,
        *,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        max_retries: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        receipt_sink: ReceiptSink,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = (connect_timeout_seconds, read_timeout_seconds)
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.receipt_sink = receipt_sink
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "golden-raspberry-queue-echo/0.1",
            }
        )
        self.sleep = sleep

    def request_json(
        self,
        method: str,
        url: str,
        *,
        request_kind: str,
        run_id: str,
        page_number: int | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> JsonResponse:
        method = method.upper()
        body_bytes = (
            json.dumps(json_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if json_body is not None
            else None
        )
        last_error: BaseException | None = None
        last_status: int | None = None
        for attempt in range(1, self.max_retries + 2):
            request_id = uuid4().hex
            started_at = iso_utc()
            started_clock = time.monotonic()
            response: Response | None = None
            raw = b""
            status = "ERROR"
            error_type: str | None = None
            error_message: str | None = None
            retryable = False
            retry_after = None
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    data=body_bytes,
                    headers={"Content-Type": "application/json"}
                    if body_bytes is not None
                    else None,
                    timeout=self.timeout,
                )
                last_status = response.status_code
                raw = response.content
                response.raise_for_status()
                payload = response.json()
                status = "SUCCESS"
                completed_at = iso_utc()
                digest = hashlib.sha256(raw).hexdigest()
                self.receipt_sink(
                    {
                        "request_id": request_id,
                        "run_id": run_id,
                        "request_kind": request_kind,
                        "page_number": page_number,
                        "attempt_number": attempt,
                        "method": method,
                        "url": url,
                        "params_json": json.dumps(params or {}, sort_keys=True, separators=(",", ":")),
                        "body_sha256": hashlib.sha256(body_bytes).hexdigest() if body_bytes is not None else None,
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                        "status": status,
                        "http_status": response.status_code,
                        "retryable": 0,
                        "retry_after_seconds": None,
                        "response_sha256": digest,
                        "response_bytes": len(raw),
                        "error_type": None,
                        "error_message": None,
                    }
                )
                return JsonResponse(
                    payload, raw, request_id, started_at, completed_at, digest
                )
            except (RequestException, ChunkedEncodingError, ValueError) as error:
                last_error = error
                error_type = type(error).__name__
                error_message = str(error)[:500]
                code = response.status_code if response is not None else None
                retryable = code is None or code == 429 or 500 <= code < 600
                retry_after = _retry_after(response)
            finally:
                if status != "SUCCESS":
                    self.receipt_sink(
                        {
                            "request_id": request_id,
                            "run_id": run_id,
                            "request_kind": request_kind,
                            "page_number": page_number,
                            "attempt_number": attempt,
                            "method": method,
                            "url": url,
                            "params_json": json.dumps(params or {}, sort_keys=True, separators=(",", ":")),
                            "body_sha256": hashlib.sha256(body_bytes).hexdigest() if body_bytes is not None else None,
                            "started_at": started_at,
                            "completed_at": iso_utc(),
                            "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                            "status": "ERROR",
                            "http_status": response.status_code if response is not None else None,
                            "retryable": int(retryable),
                            "retry_after_seconds": retry_after,
                            "response_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
                            "response_bytes": len(raw),
                            "error_type": error_type,
                            "error_message": error_message,
                        }
                    )
            if not retryable or attempt > self.max_retries:
                break
            delay = retry_after
            if delay is None:
                delay = min(
                    self.retry_max_seconds,
                    self.retry_base_seconds * (2 ** (attempt - 1)),
                )
            self.sleep(delay)
        raise PublicApiError(
            f"{request_kind} failed after bounded retries: {type(last_error).__name__ if last_error else 'unknown'}",
            http_status=last_status,
        ) from last_error


__all__ = [
    "JsonResponse",
    "PublicApiError",
    "PublicJsonTransport",
    "iso_utc",
    "utc_now",
]
