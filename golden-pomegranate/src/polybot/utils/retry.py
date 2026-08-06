"""Evidence-producing HTTP helpers with bounded GET-only retries."""

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
from requests.exceptions import ChunkedEncodingError, RequestException


EvidenceSink = Callable[[Mapping[str, Any]], None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def request_attestation_hash(
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    body: Any = None,
) -> tuple[str, str | None]:
    body_bytes = b"" if body is None else canonical_json(body).encode("utf-8")
    body_sha = hashlib.sha256(body_bytes).hexdigest() if body is not None else None
    payload = {
        "method": method.upper(),
        "url": url,
        "params": dict(params or {}),
        "body_sha256": body_sha,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest(), body_sha


@dataclass(frozen=True)
class HttpResult:
    request_id: str
    request_hash: str
    payload: Any
    content: bytes
    status_code: int
    received_at: str


def _response_content(response: Any, payload: Any) -> bytes:
    try:
        content = response.content
    except AttributeError:
        content = b""
    if isinstance(content, str):
        content = content.encode("utf-8")
    if content:
        return bytes(content)
    return canonical_json(payload).encode("utf-8")


def _retry_after_seconds(headers: Mapping[str, Any] | None) -> float | None:
    """Parse Retry-After delta seconds or HTTP date without trusting infinity."""
    if not headers:
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(raw))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            seconds = (target - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _emit(sink: EvidenceSink | None, record: Mapping[str, Any]) -> None:
    if sink is not None:
        sink(record)


def _base_record(
    *,
    request_id: str,
    run_id: str | None,
    sweep_attempt_id: str | None,
    request_kind: str,
    page_number: int | None,
    attempt_number: int,
    method: str,
    url: str,
    params: Mapping[str, Any] | None,
    body_sha256: str | None,
    request_hash: str,
    started_at: str,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "run_id": run_id,
        "sweep_attempt_id": sweep_attempt_id,
        "request_kind": request_kind,
        "page_number": page_number,
        "attempt_number": attempt_number,
        "method": method,
        "url": url,
        "params_json": canonical_json(dict(params or {})),
        "body_sha256": body_sha256,
        "request_hash": request_hash,
        "started_at": started_at,
    }


def get_json_with_retry(
    session: requests.Session,
    url: str,
    *,
    params: Mapping[str, Any] | None,
    timeout: tuple[float, float],
    max_attempts: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
    evidence_sink: EvidenceSink | None,
    run_id: str | None,
    sweep_attempt_id: str | None,
    request_kind: str,
    page_number: int | None = None,
    method: str = "GET",
) -> HttpResult:
    """Perform a public GET, retrying transient HTTP and transport failures.

    Retrying is deliberately GET only. The public ``POST /books`` endpoint has
    a separate one-shot helper so a future caller cannot generalize this policy
    to state-changing POST requests.
    """
    if method.upper() != "GET":
        raise ValueError("retry helper is GET only")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    request_hash, body_sha = request_attestation_hash("GET", url, params=params)
    last_error: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        request_id = str(uuid4())
        started_at = utc_now()
        started_clock = time.monotonic()
        base = _base_record(
            request_id=request_id,
            run_id=run_id,
            sweep_attempt_id=sweep_attempt_id,
            request_kind=request_kind,
            page_number=page_number,
            attempt_number=attempt,
            method="GET",
            url=url,
            params=params,
            body_sha256=body_sha,
            request_hash=request_hash,
            started_at=started_at,
        )
        try:
            response = session.get(url, params=dict(params or {}), timeout=timeout)
            status_code = int(getattr(response, "status_code", 200))
            if status_code >= 400:
                error = requests.HTTPError(f"HTTP {status_code} from public endpoint")
                error.response = response
                raise error
            payload = response.json()
            content = _response_content(response, payload)
            received_at = utc_now()
            _emit(
                evidence_sink,
                {
                    **base,
                    "completed_at": received_at,
                    "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                    "status": "SUCCESS",
                    "http_status": status_code,
                    "retryable": 0,
                    "retry_after_seconds": None,
                    "response_sha256": hashlib.sha256(content).hexdigest(),
                    "response_bytes": len(content),
                    "error_type": None,
                    "error_message": None,
                },
            )
            return HttpResult(
                request_id=request_id,
                request_hash=request_hash,
                payload=payload,
                content=content,
                status_code=status_code,
                received_at=received_at,
            )
        except (ChunkedEncodingError, RequestException) as error:
            last_error = error
            response = getattr(error, "response", None)
            status_code = (
                int(getattr(response, "status_code")) if response is not None else None
            )
            retryable = (
                status_code is None or status_code in {403, 429} or status_code >= 500
            )
            retry_after = _retry_after_seconds(
                getattr(response, "headers", None) if response is not None else None
            )
            completed_at = utc_now()
            _emit(
                evidence_sink,
                {
                    **base,
                    "completed_at": completed_at,
                    "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                    "status": "REQUEST_ERROR" if status_code is None else "HTTP_ERROR",
                    "http_status": status_code,
                    "retryable": int(retryable),
                    "retry_after_seconds": retry_after,
                    "response_sha256": None,
                    "response_bytes": None,
                    "error_type": type(error).__name__,
                    "error_message": " ".join(str(error).splitlines())[:500],
                },
            )
            if not retryable or attempt == max_attempts:
                raise
            exponential = min(
                max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1))
            )
            delay = max(exponential, retry_after or 0.0)
            time.sleep(min(delay, max_delay_seconds))

    assert last_error is not None
    raise last_error


def post_json_once(
    session: requests.Session,
    url: str,
    *,
    body: Any,
    timeout: tuple[float, float],
    evidence_sink: EvidenceSink | None,
    run_id: str | None,
    sweep_attempt_id: str | None,
    request_kind: str,
    page_number: int | None = None,
) -> HttpResult:
    """Perform the public batch-book POST exactly once and attest the attempt."""
    request_hash, body_sha = request_attestation_hash("POST", url, body=body)
    request_id = str(uuid4())
    started_at = utc_now()
    started_clock = time.monotonic()
    base = _base_record(
        request_id=request_id,
        run_id=run_id,
        sweep_attempt_id=sweep_attempt_id,
        request_kind=request_kind,
        page_number=page_number,
        attempt_number=1,
        method="POST",
        url=url,
        params=None,
        body_sha256=body_sha,
        request_hash=request_hash,
        started_at=started_at,
    )
    try:
        response = session.post(url, json=body, timeout=timeout)
        status_code = int(getattr(response, "status_code", 200))
        if status_code >= 400:
            error = requests.HTTPError(f"HTTP {status_code} from public endpoint")
            error.response = response
            raise error
        payload = response.json()
        content = _response_content(response, payload)
        received_at = utc_now()
        _emit(
            evidence_sink,
            {
                **base,
                "completed_at": received_at,
                "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                "status": "SUCCESS",
                "http_status": status_code,
                "retryable": 0,
                "retry_after_seconds": None,
                "response_sha256": hashlib.sha256(content).hexdigest(),
                "response_bytes": len(content),
                "error_type": None,
                "error_message": None,
            },
        )
        return HttpResult(
            request_id=request_id,
            request_hash=request_hash,
            payload=payload,
            content=content,
            status_code=status_code,
            received_at=received_at,
        )
    except (ChunkedEncodingError, RequestException) as error:
        response = getattr(error, "response", None)
        status_code = (
            int(getattr(response, "status_code")) if response is not None else None
        )
        completed_at = utc_now()
        _emit(
            evidence_sink,
            {
                **base,
                "completed_at": completed_at,
                "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                "status": "REQUEST_ERROR" if status_code is None else "HTTP_ERROR",
                "http_status": status_code,
                "retryable": 0,
                "retry_after_seconds": _retry_after_seconds(
                    getattr(response, "headers", None) if response is not None else None
                ),
                "response_sha256": None,
                "response_bytes": None,
                "error_type": type(error).__name__,
                "error_message": " ".join(str(error).splitlines())[:500],
            },
        )
        raise


__all__ = [
    "HttpResult",
    "canonical_json",
    "get_json_with_retry",
    "post_json_once",
    "request_attestation_hash",
    "utc_now",
]
