"""Bounded public JSON transport with attempt-level evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

import requests
from requests import Response
from requests.exceptions import ChunkedEncodingError, RequestException


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    current = value or utc_now()
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


class PublicApiError(RuntimeError):
    """A bounded public request failed or returned an invalid response."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


@dataclass(frozen=True)
class JsonResponse:
    payload: Any
    raw: bytes
    request_id: str
    request_hash: str
    started_at: str
    received_at: str
    response_sha256: str


def _retry_after(response: Response | None, now: datetime) -> float | None:
    if response is None:
        return None
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = (parsed.astimezone(timezone.utc) - now).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


class PublicJsonTransport:
    """Credential-free requests transport with fail-closed evidence recording."""

    def __init__(
        self,
        *,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        max_retries: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        receipt_sink: Callable[[Mapping[str, Any]], None],
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.connect_timeout_seconds = connect_timeout_seconds
        self.read_timeout_seconds = read_timeout_seconds
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.receipt_sink = receipt_sink
        self.sleep = sleep
        self.session = session or requests.Session()
        # Public collection must not inherit .netrc credentials or proxy auth.
        self.session.trust_env = False
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "golden-strawberry-last-mile/0.1",
            }
        )

    def request_json(
        self,
        method: str,
        url: str,
        *,
        request_kind: str,
        run_id: str,
        page_number: int | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> JsonResponse:
        method = method.upper()
        body_bytes = (
            canonical_json(json_body).encode("utf-8") if json_body is not None else None
        )
        request_material = canonical_json(
            {
                "method": method,
                "url": url,
                "params": dict(params or {}),
                "body_sha256": (
                    hashlib.sha256(body_bytes).hexdigest() if body_bytes else None
                ),
            }
        )
        request_hash = hashlib.sha256(request_material.encode("utf-8")).hexdigest()
        last_error: BaseException | None = None
        last_status: int | None = None

        for attempt_number in range(1, self.max_retries + 2):
            request_id = uuid4().hex
            started_clock = time.monotonic()
            started_at = iso_utc()
            response: Response | None = None
            raw = b""
            retryable = False
            retry_after_seconds: float | None = None
            error_type: str | None = None
            error_message: str | None = None
            try:
                response = self.session.request(
                    method,
                    url,
                    params=dict(params or {}),
                    json=json_body,
                    timeout=(
                        self.connect_timeout_seconds,
                        self.read_timeout_seconds,
                    ),
                )
                last_status = response.status_code
                raw = response.content
                if response.status_code >= 400:
                    response.raise_for_status()
                try:
                    payload = json.loads(
                        raw.decode("utf-8"),
                        parse_constant=lambda value: (_ for _ in ()).throw(
                            ValueError(f"non-finite JSON constant: {value}")
                        ),
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                    raise ValueError(
                        "public endpoint returned malformed JSON"
                    ) from error
                received_at = iso_utc()
                response_sha256 = hashlib.sha256(raw).hexdigest()
                self.receipt_sink(
                    {
                        "request_id": request_id,
                        "run_id": run_id,
                        "request_kind": request_kind,
                        "page_number": page_number,
                        "attempt_number": attempt_number,
                        "method": method,
                        "url": url,
                        "params_json": canonical_json(dict(params or {})),
                        "body_sha256": (
                            hashlib.sha256(body_bytes).hexdigest()
                            if body_bytes is not None
                            else None
                        ),
                        "request_hash": request_hash,
                        "started_at": started_at,
                        "completed_at": received_at,
                        "elapsed_ms": round(
                            (time.monotonic() - started_clock) * 1000, 3
                        ),
                        "status": "SUCCESS",
                        "http_status": response.status_code,
                        "retryable": 0,
                        "retry_after_seconds": None,
                        "response_sha256": response_sha256,
                        "response_bytes": len(raw),
                        "error_type": None,
                        "error_message": None,
                    }
                )
                return JsonResponse(
                    payload=payload,
                    raw=raw,
                    request_id=request_id,
                    request_hash=request_hash,
                    started_at=started_at,
                    received_at=received_at,
                    response_sha256=response_sha256,
                )
            except (ChunkedEncodingError, RequestException, ValueError) as error:
                last_error = error
                status = response.status_code if response is not None else None
                last_status = status
                retryable = (
                    status is None
                    or status == 429
                    or (status is not None and 500 <= status < 600)
                    or isinstance(error, (ChunkedEncodingError, ValueError))
                )
                retry_after_seconds = _retry_after(response, utc_now())
                error_type = type(error).__name__
                error_message = " ".join(str(error).splitlines())[:500]
                completed_at = iso_utc()
                self.receipt_sink(
                    {
                        "request_id": request_id,
                        "run_id": run_id,
                        "request_kind": request_kind,
                        "page_number": page_number,
                        "attempt_number": attempt_number,
                        "method": method,
                        "url": url,
                        "params_json": canonical_json(dict(params or {})),
                        "body_sha256": (
                            hashlib.sha256(body_bytes).hexdigest()
                            if body_bytes is not None
                            else None
                        ),
                        "request_hash": request_hash,
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "elapsed_ms": round(
                            (time.monotonic() - started_clock) * 1000, 3
                        ),
                        "status": "ERROR",
                        "http_status": status,
                        "retryable": int(retryable),
                        "retry_after_seconds": retry_after_seconds,
                        "response_sha256": (
                            hashlib.sha256(raw).hexdigest() if raw else None
                        ),
                        "response_bytes": len(raw),
                        "error_type": error_type,
                        "error_message": error_message,
                    }
                )
            if not retryable or attempt_number > self.max_retries:
                break
            delay = retry_after_seconds
            if delay is None:
                delay = self.retry_base_seconds * (2 ** (attempt_number - 1))
            self.sleep(min(self.retry_max_seconds, max(0.0, delay)))

        raise PublicApiError(
            f"{request_kind} failed after bounded retries: "
            f"{type(last_error).__name__ if last_error else 'unknown'}",
            http_status=last_status,
        ) from last_error


__all__ = [
    "JsonResponse",
    "PublicApiError",
    "PublicJsonTransport",
    "canonical_json",
    "iso_utc",
    "utc_now",
]
