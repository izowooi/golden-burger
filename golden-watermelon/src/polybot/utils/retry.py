"""Bounded credential-free HTTP transport with attempt evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

import requests
from requests.exceptions import ChunkedEncodingError, RequestException


def iso_utc(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class PublicApiError(RuntimeError):
    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id


@dataclass(frozen=True)
class JsonResponse:
    payload: Any
    raw: bytes
    request_id: str
    started_at: str
    received_at: str
    response_sha256: str


class PublicJsonTransport:
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
        self.session.trust_env = False
        self.session.headers.update({"Accept": "application/json", "User-Agent": "golden-watermelon/0.1"})

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
        last_error: BaseException | None = None
        for attempt in range(1, self.max_retries + 2):
            request_id = uuid4().hex
            started = iso_utc()
            started_clock = time.monotonic()
            response = None
            raw = b""
            try:
                response = self.session.request(
                    method.upper(), url, params=dict(params or {}), json=json_body,
                    timeout=(self.connect_timeout_seconds, self.read_timeout_seconds),
                )
                raw = response.content
                response.raise_for_status()
                payload = json.loads(raw.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                received = iso_utc()
                digest = hashlib.sha256(raw).hexdigest()
                self.receipt_sink({
                    "request_id": request_id, "run_id": run_id, "request_kind": request_kind,
                    "page_number": page_number, "attempt_number": attempt, "method": method.upper(),
                    "url": url, "params_json": canonical_json(dict(params or {})),
                    "body_sha256": hashlib.sha256(canonical_json(json_body).encode()).hexdigest() if json_body is not None else None,
                    "started_at": started, "completed_at": received,
                    "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                    "status": "SUCCESS", "http_status": response.status_code,
                    "response_sha256": digest, "response_bytes": len(raw),
                    "error_type": None, "error_message": None,
                })
                return JsonResponse(payload, raw, request_id, started, received, digest)
            except (ChunkedEncodingError, RequestException, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                last_error = error
                status = response.status_code if response is not None else None
                retryable = status is None or status == 429 or (status is not None and 500 <= status < 600) or isinstance(error, (ChunkedEncodingError, ValueError))
                completed = iso_utc()
                self.receipt_sink({
                    "request_id": request_id, "run_id": run_id, "request_kind": request_kind,
                    "page_number": page_number, "attempt_number": attempt, "method": method.upper(),
                    "url": url, "params_json": canonical_json(dict(params or {})),
                    "body_sha256": hashlib.sha256(canonical_json(json_body).encode()).hexdigest() if json_body is not None else None,
                    "started_at": started, "completed_at": completed,
                    "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                    "status": "FAILED", "http_status": status,
                    "response_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
                    "response_bytes": len(raw), "error_type": type(error).__name__,
                    "error_message": " ".join(str(error).splitlines())[:500],
                })
                if not retryable or attempt > self.max_retries:
                    break
                delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** (attempt - 1)))
                self.sleep(delay)
        raise PublicApiError(f"public request failed: {type(last_error).__name__}: {last_error}")
